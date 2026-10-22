"""Split post presentation against the actual provider request envelope."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from .markdown_post import lark_markdown_post_content
from ..outbound import LARK_POST_REQUEST_MAX_BYTES, LarkOutboundTextError


def post_request_bytes(text: str, template: Mapping[str, Any]) -> int:
    content = json.loads(lark_markdown_post_content(text))
    authored = template.get("content", {})
    if isinstance(authored, str):
        parsed = json.loads(authored)
        # Preserve the provider preview's encoding and whitespace, including
        # attachment merging. Escaped Unicode can cost twice its UTF-8 form.
        old_text = parsed.get("zh_cn", {}).get("content", [[{}]])[0][0].get("text")
        if isinstance(old_text, str):
            new_text = content["zh_cn"]["content"][0][0]["text"]
            for ascii_only in (False, True):
                old_literal = json.dumps(old_text, ensure_ascii=ascii_only)
                if old_literal in authored:
                    replacement = json.dumps(new_text, ensure_ascii=ascii_only)
                    body = {
                        **template,
                        "content": authored.replace(old_literal, replacement, 1),
                    }
                    return len(
                        json.dumps(
                            body, ensure_ascii=False, separators=(",", ":")
                        ).encode("utf-8")
                    )
        authored = parsed
    if isinstance(authored, Mapping):
        content.update(
            {key: value for key, value in authored.items() if key != "zh_cn"}
        )
    body = {
        **template,
        "content": json.dumps(content, ensure_ascii=False, separators=(",", ":")),
    }
    return len(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _blocks(text: str) -> list[str]:
    """Keep ordinary paragraphs, code blocks and tables together when possible."""
    lines = text.splitlines(keepends=True)
    result: list[str] = []
    index = 0
    while index < len(lines):
        start = index
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})", lines[index])
        if fence:
            index += 1
            while index < len(lines):
                close = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", lines[index])
                index += 1
                if (
                    close
                    and close[1][0] == fence[1][0]
                    and len(close[1]) >= len(fence[1])
                ):
                    break
        else:
            index += 1
            while (
                index < len(lines)
                and lines[index].strip()
                and not re.match(r"^ {0,3}(`{3,}|~{3,})", lines[index])
            ):
                index += 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        result.append("".join(lines[start:index]))
    return result


def _protected_spans(text: str) -> list[tuple[int, int]]:
    # Do not cut a normal link, escaped character, inline code or emphasis.
    return [
        (m.start(), m.end())
        for m in re.finditer(
            r"\\.|(`+)(?:(?!\1).)*?\1|!?\[[^\]\n]*\]\((?:\\.|[^)\n])*\)|\*\*.+?\*\*|__.+?__",
            text,
        )
    ]


def _large_block(block: str, fits, first_prefix: str = "") -> list[str]:
    """Repeat only presentation context for oversized code/table blocks."""
    lines = block.splitlines(keepends=True)
    prefix = suffix = ""
    fence = re.match(r"^ {0,3}(`{3,}|~{3,})[^\n]*\n", block)
    if fence:
        prefix = fence[0]
        close = re.search(
            r"(?m)^ {0,3}" + re.escape(fence[1]) + r"\s*$", block[fence.end() :]
        )
        if close:
            body = block[fence.end() : fence.end() + close.start()]
            suffix = "\n" + fence[1]
        else:
            body = block[fence.end() :]
            suffix = "\n" + fence[1]
    elif len(lines) >= 3 and "|" in lines[0] and re.fullmatch(r"[ |:\-]+\n?", lines[1]):
        # Each continuation repeats the column names, not the data rows.
        prefix = "".join(lines[:2])
        rows, current = [], ""

        def row_fits(value):
            return fits((first_prefix if not rows else "") + value)

        for line in lines[2:]:
            if not fits(prefix + line):
                raise LarkOutboundTextError(
                    "one rich table row exceeds the provider message bound"
                )
            if current and not row_fits(prefix + current + line):
                rows.append((first_prefix if not rows else "") + prefix + current)
                current = ""
            if not current and not row_fits(prefix + line) and not rows:
                rows.append(first_prefix)
                first_prefix = ""
            current += line
        if current:
            rows.append((first_prefix if not rows else "") + prefix + current)
        return rows
    else:
        body = block
        wrapped = re.fullmatch(r"(\*\*|__|`+)([\s\S]+)\1\s*", block)
        if wrapped:
            prefix = suffix = wrapped[1]
            body = wrapped[2]
    result = []
    while body:
        low, high = 0, len(body)
        while low < high:
            middle = (low + high + 1) // 2
            if fits(
                (first_prefix if not result else "") + prefix + body[:middle] + suffix
            ):
                low = middle
            else:
                high = middle - 1
        cut = low
        if fence:
            newline = body.rfind("\n", 0, cut)
            if newline >= 0:
                cut = newline + 1
        else:
            for start, end in _protected_spans(body):
                if start < cut < end:
                    cut = start
                    break
        if cut <= 0:
            if first_prefix and not result:
                # A protected element may fit a fresh post even when the
                # preceding paragraph leaves too little room for it.
                result.append(first_prefix)
                first_prefix = ""
                continue
            raise LarkOutboundTextError(
                "one rich inline element exceeds the provider message bound"
            )
        result.append(
            (first_prefix if not result else "") + prefix + body[:cut] + suffix
        )
        body = body[cut:]
    return result


def split_lark_markdown_posts(text: str, template: Mapping[str, Any]) -> list[str]:
    """Use the provider's full byte budget, including JSON escapes and markers.

    No character ceiling, fixed part count, truncation or text fallback. The
    ordinary single-post serializer and readback still own each message.
    """
    if post_request_bytes(text, template) <= LARK_POST_REQUEST_MAX_BYTES:
        return [text]
    total = 2
    while True:
        pieces: list[str] = []
        current = ""

        def fits(body: str) -> bool:
            marked = f"（{total}/{total}）\n\n" + body
            return post_request_bytes(marked, template) <= LARK_POST_REQUEST_MAX_BYTES

        for block in _blocks(text):
            if fits(current + block):
                current += block
                continue
            if not fits(block):
                pieces.extend(_large_block(block, fits, first_prefix=current))
                current = ""
            else:
                if current:
                    pieces.append(current)
                current = block
        if current:
            pieces.append(current)
        if len(pieces) == total:
            break
        total = len(pieces)
    parts = [f"（{i}/{total}）\n\n{part}" for i, part in enumerate(pieces, 1)]
    if any(
        post_request_bytes(part, template) > LARK_POST_REQUEST_MAX_BYTES
        for part in parts
    ):
        raise LarkOutboundTextError("rich part exceeds the provider request bound")
    return parts
