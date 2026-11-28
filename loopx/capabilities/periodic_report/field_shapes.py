"""One owner for the field rules a periodic report is validated against.  Refs #4447 Track A.

Three decisions were each stated twice on the base revision.  ``adapters.py`` and
``project_progress.py`` both compiled the language-tag rule, and both listed the
content kinds that are supporting by nature -- the two set literals even spelled
their members in a different order, which a value-keyed duplicate scan reads as two
different values.  The identity rule that gates ``goal_id``, ``agent_id`` and ``failure_code`` was
compiled three times under two different binding names -- ``pending_intent.py`` and
``request_action.py`` called it ``_IDENTITY_RE``, ``cadence_journal.py`` called it
``_ID`` -- which is exactly what a name-keyed duplicate scan cannot see.

Every one of those sites only asks membership or ``fullmatch``, so importing the
owner here is behaviour-preserving, while a widening in one copy no longer leaves
the other behind silently.

``incremental.py`` keeps its own ``_IDENTITY_RE``.  It is a *different* rule --
case-insensitive and up to 160 characters versus lowercase-led 3..128 -- so it is
bounded-context reuse of one name, not a copy, and it is pinned as distinct here
rather than merged away by renaming.

The document schema tag and the item-visibility pair joined the owner later:
``adapters.py`` and ``audience.py`` each stated the schema string and each listed
the two visibilities, so a schema bump or a new visibility had two places to land.
``adapters.DOCUMENT_SCHEMA`` stays importable from ``adapters`` because the
bindings, archive and renderer faces read it from there.
"""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

from ...public_safe_text import (
    PRESENTATION_LOCAL_PATH_PATTERNS,
    SECRET_LIKE_SURFACE_PATTERN,
)

SUPPORTING_CONTENT_KINDS = frozenset({"coverage", "delivery_receipt", "runtime"})
REPORT_IDENTITY_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")
REPORT_LANGUAGE_PATTERN = re.compile(r"^[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*$")
DOCUMENT_SCHEMA = "periodic_report_document_v0"
ITEM_VISIBILITIES = frozenset({"primary", "supporting"})
INLINE_MARKDOWN_LINK_RAW_MULTIPLIER = 6


def validate_periodic_report_http_url(
    value: object,
    *,
    reject_secret_shapes: bool = False,
) -> str | None:
    """Validate one display-safe HTTP(S) URL without dereferencing it."""

    if not isinstance(value, str) or not value or len(value) > 500:
        return None
    if value != value.strip():
        return None
    if any(
        char.isspace()
        or unicodedata.category(char).startswith("C")
        or char in "<>\\`"
        for char in value
    ):
        return None
    if any(pattern.search(value) for pattern in PRESENTATION_LOCAL_PATH_PATTERNS):
        return None
    if reject_secret_shapes and SECRET_LIKE_SURFACE_PATTERN.search(value) is not None:
        return None
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        parsed.port  # Reject malformed ports rather than linking a different address.
    except ValueError:
        return None
    return value


def iter_periodic_report_inline_segments(
    value: object,
) -> list[tuple[str, str | None]]:
    """Split prose into plain-text runs and validated ``[label](http(s)://...)`` links."""

    text = str(value or "")
    if not text:
        return []
    segments: list[tuple[str, str | None]] = []
    plain_parts: list[str] = []
    length = len(text)
    cursor = 0

    def flush_plain() -> None:
        if plain_parts:
            segments.append(("".join(plain_parts), None))
            plain_parts.clear()

    while cursor < length:
        if text[cursor] != "[":
            plain_parts.append(text[cursor])
            cursor += 1
            continue
        label_end = cursor + 1
        nested_bracket = -1
        while label_end < length and text[label_end] != "]":
            if text[label_end] in "\r\n":
                label_end = length
                break
            if text[label_end] == "[":
                nested_bracket = label_end
                break
            label_end += 1
        if nested_bracket != -1:
            plain_parts.append(text[cursor:nested_bracket])
            cursor = nested_bracket
            continue
        if (
            label_end >= length
            or label_end == cursor + 1
            or label_end - (cursor + 1) > 240
            or label_end + 1 >= length
            or text[label_end + 1] != "("
        ):
            plain_parts.append(text[cursor])
            cursor += 1
            continue
        dest_start = label_end + 2
        dest_end = dest_start
        paren_depth = 0
        matched_close = False
        while dest_end < length and dest_end - dest_start <= 500:
            char = text[dest_end]
            if char.isspace() or char in "<>\\`":
                break
            if char == "(":
                paren_depth += 1
                if paren_depth > 2:
                    break
            elif char == ")":
                if paren_depth == 0:
                    matched_close = True
                    break
                paren_depth -= 1
            dest_end += 1
        if matched_close and dest_end > dest_start:
            candidate_url = validate_periodic_report_http_url(
                text[dest_start:dest_end],
                reject_secret_shapes=True,
            )
            if candidate_url is not None:
                flush_plain()
                segments.append((text[cursor + 1 : label_end], candidate_url))
                cursor = dest_end + 1
                continue
        plain_parts.append(text[cursor])
        cursor += 1

    flush_plain()
    return segments


def periodic_report_visible_text(value: object) -> str:
    """Return display-visible text with validated inline HTTP URL targets stripped."""

    return "".join(label for label, _url in iter_periodic_report_inline_segments(value))
