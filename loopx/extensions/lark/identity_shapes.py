"""One owner for the whole-value shapes of Lark platform identifiers.

A card callback names four identifiers: the event that triggered it, the message
the card lives in, the chat that message belongs to, and the operator who
clicked. Two callback validators each decided independently what a valid one
looks like, so a shape fix in one of them silently leaves the other behind.

Searching for an identifier inside larger text is the same decision about a
different question, so it is stated here too and nowhere else:
``goal_channel_transport`` and ``event_inbox`` each compiled the unanchored
spelling themselves while fifteen and thirteen modules respectively imported it
from them, which is how a shape fix could land in one and be missed in the
other.  The unanchored forms are not interchangeable with the anchored ones --
``goal_channel_contracts`` and ``goal_channel_notification`` ``search()`` for an
id inside payload text, where ``^`` and ``$`` would change the answer -- so both
spellings stay distinct and one module decides both.

The fifth shape is the application id, ``cli_``-prefixed. It has only the
whole-value spelling because every caller applies ``fullmatch``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

LARK_EVENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,240}$")
LARK_MESSAGE_ID_PATTERN = re.compile(r"^om_[A-Za-z0-9_-]+$")
LARK_CHAT_ID_PATTERN = re.compile(r"^oc_[A-Za-z0-9_-]+$")
LARK_OPEN_ID_PATTERN = re.compile(r"^ou_[A-Za-z0-9_-]+$")
# The application a bot belongs to. Four sites decided this themselves -- the
# transport hub, ``bot_scopes``, ``event_collector_runtime`` and
# ``goal_channel_delivery_contract``, the last one with its own anchors on the
# same body -- and eight more modules reach the decision by importing the
# transport hub's name, so a fix to the body had three possible homes.
# Every caller applies ``fullmatch``, which is why only the whole-value spelling
# is stated here.
LARK_APP_ID_PATTERN = re.compile(r"^cli_[A-Za-z0-9_-]+$")

LARK_MESSAGE_ID_SEARCH = re.compile(r"om_[A-Za-z0-9_-]+")
LARK_CHAT_ID_SEARCH = re.compile(r"oc_[A-Za-z0-9_-]+")
LARK_OPEN_ID_SEARCH = re.compile(r"ou_[A-Za-z0-9_-]+")

CARD_CALLBACK_IDENTITY_SHAPES = (
    ("event_id", LARK_EVENT_ID_PATTERN),
    ("message_id", LARK_MESSAGE_ID_PATTERN),
    ("chat_id", LARK_CHAT_ID_PATTERN),
    ("operator_id", LARK_OPEN_ID_PATTERN),
)


def require_card_callback_identity(
    event: Mapping[str, Any], *, error_prefix: str
) -> None:
    """Reject a card callback whose identity fields are not whole Lark ids."""

    for field, pattern in CARD_CALLBACK_IDENTITY_SHAPES:
        if not pattern.fullmatch(str(event.get(field) or "")):
            raise ValueError(f"{error_prefix} {field} is invalid")
