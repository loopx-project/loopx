"""Text transport budgets count UTF-8 serialized request bytes, not characters."""

import pytest

from loopx.extensions.lark.outbound import (
    LarkOutboundTextError,
    validate_lark_text_request_size,
)


@pytest.mark.parametrize("value", ["x" * 149992, "测" * 49997, "😀" * 37498],
                         ids=["ascii", "cjk", "emoji"])
def test_provider_byte_budget_accepts_large_text(value):
    # {"x":""} costs eight bytes; UTF-8 code points have different costs.
    validate_lark_text_request_size({"x": value})


@pytest.mark.parametrize("value", ["x" * 149993, "测" * 49998, "😀" * 37499],
                         ids=["ascii", "cjk", "emoji"])
def test_provider_byte_budget_rejects_oversize_utf8(value):
    with pytest.raises(LarkOutboundTextError):
        validate_lark_text_request_size({"x": value})


def test_request_budget_counts_escaping_and_envelope():
    validate_lark_text_request_size({"x": '"' * 74996})
    with pytest.raises(LarkOutboundTextError):
        validate_lark_text_request_size({"x": '"' * 74997})
    with pytest.raises(LarkOutboundTextError):
        validate_lark_text_request_size({"x": "x" * 149992, "uuid": "reply-identity"})
