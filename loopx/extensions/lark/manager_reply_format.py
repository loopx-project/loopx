"""Repair the mechanically fixable rich-text defects in a manager answer.

A steward answer reaches Lark as provider text.  When the model or the
transport hands over the presentation as one line of literal ``\\n`` tokens, the
strict outbound validator rejects the whole message and the recovery path
degrades the answer to plain text: the reader loses the markdown bullet list
and the delivery is recorded as degraded.  An unresolved template placeholder
such as ``{new_description}`` passes that validator and reaches the reader as
raw braces.

This module owns the repair that runs *before* the first send: escaped newlines
outside fenced code become real newlines, so the answer's bullet list renders as
a real list, and an unresolved ``{lower_snake_case}`` placeholder becomes a
typed marker the reader can act on instead of a template artifact.  Fenced code
is left untouched, nothing is reworded, and every repair is returned as a typed
incident so the durable delivery state can stay truthful about what the reader
actually received.
"""

from __future__ import annotations

import re
from typing import Any

from .outbound import FENCED_CODE_PATTERN

RICH_TEXT_REPAIR_SCHEMA = "lark_manager_reply_rich_text_repair_v0"
ESCAPED_NEWLINE_CODE = "escaped_newline"
TEMPLATE_PLACEHOLDER_CODE = "unresolved_template_placeholder"

# A placeholder is what a template engine would have filled: one lower
# snake_case identifier in braces.  Prose braces with spaces, and the real code
# a reader may legitimately be shown, do not match and are not rewritten.
TEMPLATE_PLACEHOLDER_PATTERN = re.compile(r"\{(?P<name>[a-z][a-z0-9_]{2,})\}")
TEMPLATE_PLACEHOLDER_MARKER = "[未解析占位符: {name}]"


def repair_manager_reply_text(value: Any) -> tuple[str, list[dict[str, Any]]]:
    """Return the repaired answer body and the typed repairs that were applied.

    The repair is presentation-only and idempotent: repairing an already
    repaired body changes nothing and reports no incident.  Callers persist the
    returned body, so a later resend of the saved answer cannot regress to the
    unrepaired text.
    """

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    escaped_newlines = 0
    placeholder_names: list[str] = []

    def repair_outside_code(fragment: str) -> str:
        nonlocal escaped_newlines
        escaped_newlines += fragment.count("\\n")
        plain = fragment.replace("\\n", "\n")

        def replace_placeholder(match: re.Match[str]) -> str:
            name = match.group("name")
            if name not in placeholder_names:
                placeholder_names.append(name)
            return TEMPLATE_PLACEHOLDER_MARKER.format(name=name)

        return TEMPLATE_PLACEHOLDER_PATTERN.sub(replace_placeholder, plain)

    chunks: list[str] = []
    cursor = 0
    for fenced in FENCED_CODE_PATTERN.finditer(text):
        chunks.append(repair_outside_code(text[cursor : fenced.start()]))
        chunks.append(fenced.group(0))
        cursor = fenced.end()
    chunks.append(repair_outside_code(text[cursor:]))
    repaired = "".join(chunks)

    incidents: list[dict[str, Any]] = []
    if escaped_newlines:
        incidents.append(
            {
                "schema_version": RICH_TEXT_REPAIR_SCHEMA,
                "code": ESCAPED_NEWLINE_CODE,
                "count": escaped_newlines,
            }
        )
    if placeholder_names:
        incidents.append(
            {
                "schema_version": RICH_TEXT_REPAIR_SCHEMA,
                "code": TEMPLATE_PLACEHOLDER_CODE,
                "count": len(placeholder_names),
                "names": placeholder_names,
            }
        )
    return repaired, incidents
