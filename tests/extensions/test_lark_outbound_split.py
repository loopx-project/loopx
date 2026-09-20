"""Bounded, ordered delivery of an answer that exceeds the outbound limit."""

from __future__ import annotations

import pytest

from loopx.extensions.lark.outbound import (
    DEFAULT_LARK_TEXT_LIMIT,
    LarkOutboundTextError,
    split_lark_outbound_text,
)


def test_body_within_the_limit_is_one_unchanged_part() -> None:
    assert split_lark_outbound_text("完整答复", limit=1200) == ["完整答复"]


def test_parts_are_bounded_ordered_and_lossless() -> None:
    body = "".join(f"第{index:03d}段" + "内容" * 40 + "\n" for index in range(30))
    parts = split_lark_outbound_text(body, limit=200)
    assert len(parts) > 1
    assert [part.split(" ", 1)[0] for part in parts] == [
        f"({index}/{len(parts)})" for index in range(1, len(parts) + 1)
    ]
    for part in parts:
        assert len(part) <= 200, part
    # Removing the markers restores the validated body exactly: no reflowing,
    # no abbreviation, and no dropped line.
    restored = "".join(part.split(" ", 1)[1] for part in parts)
    assert restored == split_lark_outbound_text(body, limit=100_000)[0]


def test_a_single_unbroken_line_is_hard_split() -> None:
    body = "x" * 5000
    parts = split_lark_outbound_text(body, limit=300)
    assert len(parts) == 18
    assert all(len(part) <= 300 for part in parts)
    assert "".join(part.split(" ", 1)[1] for part in parts) == body


def test_max_parts_replaces_the_remainder_with_the_overflow_note() -> None:
    body = "y" * 5000
    parts = split_lark_outbound_text(
        body, limit=300, max_parts=4, overflow_note="答复过长，完整内容在管家会话。"
    )
    assert len(parts) == 4
    assert parts[-1].startswith("(4/4) ")
    assert "完整内容在管家会话" in parts[-1]
    assert all(len(part) <= 300 for part in parts)
    # The kept prefix is a real prefix of the body, not a resampled summary.
    kept = "".join(part.split(" ", 1)[1] for part in parts[:-1])
    assert body.startswith(kept)


def test_invalid_bounds_and_missing_note_are_refused() -> None:
    with pytest.raises(ValueError):
        split_lark_outbound_text("body", limit=0)
    with pytest.raises(ValueError):
        split_lark_outbound_text("body", max_parts=2, overflow_note="")
    with pytest.raises(ValueError):
        split_lark_outbound_text("body", max_parts=0, overflow_note="note")
    # The content contract still runs first: a body the provider would reject is
    # never split into parts that would each be rejected too.
    with pytest.raises(LarkOutboundTextError):
        split_lark_outbound_text(r"private\nformat", limit=1200)


def test_default_limit_matches_the_delivery_contract() -> None:
    body = "z" * (DEFAULT_LARK_TEXT_LIMIT + 1)
    parts = split_lark_outbound_text(body)
    assert len(parts) == 2
    assert all(len(part) <= DEFAULT_LARK_TEXT_LIMIT for part in parts)
