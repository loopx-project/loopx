"""Lark post presentation; shared by all inbox reply callers.

This is provider formatting, not conversation state or effect authority. The
post renderer rejects some closing strong delimiters between punctuation and
a following word or non-ASCII symbol (for example a fullwidth separator). Move
that trailing punctuation outside the emphasis;
visible text is unchanged and inline/fenced code remains authored.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any


def normalize_lark_markdown_emphasis(text: str) -> str:
    """Repair paired strong spans at provider punctuation boundaries only.

    This deliberately is not a new Markdown parser. The provider still owns
    Markdown rendering. Escapes, code, link destinations, unmatched markers and
    triple-star runs remain opaque, rather than guessing their meaning.
    """
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in text.splitlines(keepends=True):
        match = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence is not None:
            lines.append(line)
            if (
                match
                and match[1][0] == fence[0]
                and len(match[1]) >= fence[1]
                and not line[match.end() :].strip()
            ):
                fence = None
            continue
        if match:
            fence = (match[1][0], len(match[1]))
            lines.append(line)
            continue
        lines.append(_normalize_strong_line(line))
    return "".join(lines)


def _normalize_strong_line(line: str) -> str:
    edits: list[tuple[int, int, str]] = []
    opening: int | None = None
    ticks = 0
    opaque_end = 0
    cursor = 0
    while cursor < len(line):
        character = line[cursor]
        if character == "\\" and not ticks:
            cursor += 2
            continue
        if character == "`":
            end = cursor + 1
            while end < len(line) and line[end] == "`":
                end += 1
            size = end - cursor
            if not ticks:
                ticks = size
            elif size == ticks:
                ticks = 0
                opaque_end = end
            cursor = end
            continue
        # URLs are opaque. Emphasis in a link label can still be repaired.
        if not ticks and line.startswith("](", cursor):
            depth = 1
            cursor += 2
            while cursor < len(line) and depth:
                if line[cursor] == "\\":
                    cursor += 2
                    continue
                if line[cursor] == "(":
                    depth += 1
                elif line[cursor] == ")":
                    depth -= 1
                cursor += 1
            opaque_end = cursor
            continue
        if ticks or character != "*":
            cursor += 1
            continue
        end = cursor + 1
        while end < len(line) and line[end] == "*":
            end += 1
        if end - cursor != 2:
            cursor = end
            continue
        if opening is None:
            if end < len(line) and not line[end].isspace():
                opening = end
        elif cursor > opening and not line[cursor - 1].isspace():
            suffix = cursor
            while (
                suffix > max(opening, opaque_end)
                and unicodedata.category(line[suffix - 1]).startswith(("P", "S"))
                and line[suffix - 1] not in "`*_\\"
            ):
                suffix -= 1
            if (
                suffix < cursor
                and suffix > opening
                and not line[suffix - 1].isspace()
                and end < len(line)
                and (
                    line[end].isalnum()
                    or (
                        not line[end].isascii()
                        and unicodedata.category(line[end]).startswith("S")
                    )
                )
            ):
                edits.append((suffix, end, "**" + line[suffix:cursor]))
            opening = None
        cursor = end
    chunks: list[str] = []
    cursor = 0
    for start, end, replacement in edits:
        chunks.extend((line[cursor:start], replacement))
        cursor = end
    chunks.append(line[cursor:])
    return "".join(chunks)


def lark_markdown_post_content(text: str) -> str:
    """Render authored Markdown without CLI image fetching or link rewriting."""
    text = normalize_lark_markdown_emphasis(text)
    return json.dumps(
        {"zh_cn": {"content": [[{"tag": "md", "text": text}]]}},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _single_markdown_post(value: Any) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, Mapping) or set(value) != {"zh_cn"}:
        return None
    locale = value["zh_cn"]
    if not isinstance(locale, Mapping) or set(locale) - {"title", "content"}:
        return None
    if locale.get("title", "") != "":
        return None
    rows = locale.get("content")
    if not isinstance(rows, list) or len(rows) != 1:
        return None
    row = rows[0]
    if not isinstance(row, list) or len(row) != 1:
        return None
    node = row[0]
    if not isinstance(node, Mapping) or set(node) != {"tag", "text"}:
        return None
    return (
        node["text"] if node["tag"] == "md" and isinstance(node["text"], str) else None
    )


def lark_markdown_preview_matches(*, text: str, payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    calls = payload.get("api")
    if calls is None and isinstance(data, Mapping):
        calls = data.get("api")
    if not isinstance(calls, list) or len(calls) != 1:
        return False
    call = calls[0]
    body = call.get("body") if isinstance(call, Mapping) else None
    return (
        isinstance(body, Mapping)
        and body.get("msg_type") == "post"
        and _single_markdown_post(body.get("content"))
        == normalize_lark_markdown_emphasis(text)
    )


def lark_markdown_readback_matches(*, text: str, message: Mapping[str, Any]) -> bool:
    """Accept the raw post or CLI's md text, never a plain-text lookalike."""
    if message.get("msg_type", message.get("message_type")) != "post":
        return False
    if message.get("mentions") not in (None, []):
        return False
    body = message.get("body")
    if isinstance(body, Mapping):
        actual = _single_markdown_post(body.get("content"))
    else:
        actual = message.get("content")
        if not isinstance(actual, str):
            actual = _single_markdown_post(actual)
    return isinstance(actual, str) and actual.replace(
        "\r\n", "\n"
    ).strip() == normalize_lark_markdown_emphasis(text)
