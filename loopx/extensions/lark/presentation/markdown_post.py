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
from xml.etree import ElementTree
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


def _single_markdown_post(value: Any, attachment_keys: tuple[str, ...] = ()) -> str | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, Mapping) or set(value) != ({"zh_cn", "files"} if attachment_keys else {"zh_cn"}):
        return None
    if attachment_keys and (not isinstance(value.get("files"), list)
                            or tuple(file.get("key") for file in value["files"] if isinstance(file, Mapping)) != attachment_keys
                            or len(value["files"]) != len(attachment_keys)):
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


def lark_markdown_preview_matches(*, text: str, payload: Mapping[str, Any], attachment_keys: tuple[str, ...] = ()) -> bool:
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
        and _single_markdown_post(body.get("content"), attachment_keys)
        == normalize_lark_markdown_emphasis(text)
    )


def _readback_post(message: Mapping[str, Any], attachment_keys: tuple[str, ...],
                   attachment_names: tuple[str, ...]) -> tuple[str, tuple[str, ...]] | None:
    if message.get("msg_type", message.get("message_type")) != "post":
        return None
    if message.get("mentions") not in (None, []):
        return None
    if attachment_names and len(attachment_names) != len(attachment_keys):
        return None
    body = message.get("body")
    actual = body.get("content") if isinstance(body, Mapping) else message.get("content")
    files = []
    if isinstance(body, Mapping) or not isinstance(actual, str):
        if isinstance(actual, str):
            try:
                actual = json.loads(actual)
            except json.JSONDecodeError:
                return None
        if isinstance(actual, Mapping) and attachment_keys:
            files = actual.get("files")
        keys = tuple(file.get("key") for file in files if isinstance(file, Mapping)) if isinstance(files, list) else ()
        actual = _single_markdown_post(actual, keys)
    else:
        if attachment_keys:
            # The CLI renders the post's attachment zone as trailing file tags.
            lines = actual.rstrip().splitlines()
            try:
                tags = [ElementTree.fromstring(tag.strip()) for tag in lines[-len(attachment_keys):]]
            except ElementTree.ParseError:
                return None
            if any(tag.tag != "file" or len(tag) or (tag.text or "").strip() or tag.tail for tag in tags):
                return None
            files = [tag.attrib for tag in tags]
            actual = "\n".join(lines[:-len(attachment_keys)]).rstrip()
    if not isinstance(actual, str) or not isinstance(files, list) or len(files) != len(attachment_keys):
        return None
    keys = []
    for index, file in enumerate(files):
        if not isinstance(file, Mapping) or not re.fullmatch(r"file_[A-Za-z0-9_-]{1,240}", str(file.get("key") or "")):
            return None
        key = file["key"]
        if attachment_names:
            # Feishu may replace upload keys with message-scoped resource keys.
            # A replacement must retain the exact ordered display name; bytes
            # are independently downloaded and hashed by the transport.
            name = file.get("name")
            if name != attachment_names[index] and not (name is None and key == attachment_keys[index]):
                return None
        elif key != attachment_keys[index]:
            return None
        keys.append(key)
    return actual, tuple(keys)


def lark_markdown_readback_attachment_keys(*, text: str, message: Mapping[str, Any],
                                         attachment_keys: tuple[str, ...] = (),
                                         attachment_names: tuple[str, ...] = ()) -> tuple[str, ...] | None:
    """Resolve keys only from the exact post; this alone never verifies files."""
    parsed = _readback_post(message, attachment_keys, attachment_names)
    if parsed is None:
        return None
    actual, keys = parsed
    return keys if actual.replace(
        "\r\n", "\n"
    ).strip() == normalize_lark_markdown_emphasis(text) else None


def lark_markdown_readback_matches(*, text: str, message: Mapping[str, Any],
                                 attachment_keys: tuple[str, ...] = (),
                                 attachment_names: tuple[str, ...] = ()) -> bool:
    """Accept the raw post or CLI's md text, never a plain-text lookalike."""
    return lark_markdown_readback_attachment_keys(text=text, message=message,
        attachment_keys=attachment_keys, attachment_names=attachment_names) is not None
