from __future__ import annotations

import html
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

MentionIdentityKind = Literal["user_id", "open_id", "union_id"]

AT_MENTION_PATTERN = re.compile(
    r'<at\s+(?P<kind>user_id|open_id|union_id)="(?P<identity>[^"<>]+)">'
    r"(?P<name>.*?)</at>",
    re.IGNORECASE,
)
MENTION_ID_KEYS = ("open_id", "user_id", "union_id")
FENCED_CODE_PATTERN = re.compile(r"```.*?```", re.DOTALL)
LITERAL_MENTION_PATTERN = re.compile(r"(?<![\w@])@[^\s@<>]+")


@dataclass(frozen=True)
class LarkMention:
    identity_kind: MentionIdentityKind
    identity: str
    display_name: str

    def markup(self) -> str:
        identity = self.identity.strip()
        display_name = self.display_name.strip()
        if self.identity_kind not in MENTION_ID_KEYS:
            raise ValueError("unsupported Lark mention identity kind")
        if not identity or any(character in identity for character in '"<>'):
            raise ValueError("Lark mention identity is invalid")
        if not display_name:
            raise ValueError("Lark mention display name is required")
        return f'<at {self.identity_kind}="{identity}">{html.escape(display_name)}</at>'


def build_lark_mention_prefix(mentions: Sequence[LarkMention]) -> str:
    if not mentions:
        return ""
    identities = [mention.identity.strip() for mention in mentions]
    if len(set(identities)) != len(identities):
        raise ValueError("Lark mention identities must be unique")
    return " ".join(mention.markup() for mention in mentions)


def expected_lark_mention_identities(text: str) -> dict[str, str]:
    expected: dict[str, str] = {}
    for match in AT_MENTION_PATTERN.finditer(text):
        identity = match.group("identity").strip()
        identity_kind = match.group("kind").lower()
        existing = expected.get(identity)
        if existing is not None and existing != identity_kind:
            raise ValueError("Lark mention identity kind is ambiguous")
        expected[identity] = identity_kind
    return expected


def lark_member_identities(value: Any) -> set[str]:
    identities: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, child in node.items():
                if key in {"member_id", *MENTION_ID_KEYS} and isinstance(child, str):
                    identity = child.strip()
                    if identity:
                        identities.add(identity)
                else:
                    visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    return identities


def _content_text(value: Any) -> str:
    if isinstance(value, Mapping):
        text = value.get("text")
        if isinstance(text, str):
            return text
        content = value.get("content")
        if content is not None:
            return _content_text(content)
        return ""
    if not isinstance(value, str):
        return ""
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return _content_text(decoded) if isinstance(decoded, Mapping) else value


def _message_text(message: Mapping[str, Any]) -> str:
    body = message.get("body")
    if isinstance(body, Mapping):
        text = _content_text(body)
        if text:
            return text
    return _content_text(message.get("content"))


def _mention_identities(mention: Mapping[str, Any]) -> set[str]:
    identities = {
        str(mention.get(key) or "").strip()
        for key in MENTION_ID_KEYS
        if str(mention.get(key) or "").strip()
    }
    mention_id = mention.get("id")
    if isinstance(mention_id, Mapping):
        identities.update(
            str(mention_id.get(key) or "").strip()
            for key in MENTION_ID_KEYS
            if str(mention_id.get(key) or "").strip()
        )
    elif isinstance(mention_id, str) and mention_id.strip():
        identities.add(mention_id.strip())
    return identities


def _canonical_expected_text(text: str) -> tuple[str, dict[str, str]]:
    identity_tokens: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        identity = match.group("identity").strip()
        token = identity_tokens.setdefault(
            identity, f"\x00mention:{len(identity_tokens)}\x00"
        )
        return token

    replaced = AT_MENTION_PATTERN.sub(replace, text)
    return normalized_lark_lines(replaced), identity_tokens


def normalized_lark_lines(value: Any) -> str:
    lines = [" ".join(line.split()) for line in str(value or "").splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines)


# Text request bodies: https://open.feishu.cn/document/server-docs/im-v1/message/reply
# Use decimal KB conservatively; count UTF-8 JSON bytes, not display characters.
LARK_TEXT_REQUEST_MAX_BYTES = 150_000
# Rich post messages have a separate provider request-body limit.
LARK_POST_REQUEST_MAX_BYTES = 30_000


class LarkOutboundTextError(ValueError):
    """A local text-format failure before any provider write."""


# The single owner of the provider's plain-text delivery limit: the validator and
# the splitter both read it, so a part can never be sized against a stale number.
DEFAULT_LARK_TEXT_LIMIT = 1200


def normalize_lark_outbound_text(
    value: Any, *, limit: int | None = DEFAULT_LARK_TEXT_LIMIT, preserve_format: bool = False,
) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    outside_code = FENCED_CODE_PATTERN.sub("", text)
    if r"\n" in outside_code:
        raise LarkOutboundTextError(
            "Lark outbound text contains a literal backslash-n outside fenced code; "
            "pass real newlines"
        )
    without_structured_mentions = AT_MENTION_PATTERN.sub("", outside_code)
    if "<at" in without_structured_mentions.lower() or "</at>" in (
        without_structured_mentions.lower()
    ):
        raise LarkOutboundTextError(
            "Lark outbound text contains a malformed or unsupported <at> node"
        )
    if LITERAL_MENTION_PATTERN.search(without_structured_mentions):
        raise LarkOutboundTextError(
            "Lark outbound notification contains a literal @ mention; resolve the "
            "member identity and use a structured <at ...> node"
        )
    normalized = text.strip() if preserve_format else normalized_lark_lines(text)
    if limit is not None and len(normalized) > limit:
        raise LarkOutboundTextError(
            f"Lark outbound text exceeds the {limit}-character delivery limit"
        )
    return normalized


# A part marker such as "(12/345)" costs a fixed budget at the head of every
# part. Reserving it before packing keeps every part inside the provider limit
# without a second pass that could overflow the last part.
LARK_PART_MARKER_BUDGET = len("(999/999) ")


def split_lark_outbound_text(
    value: Any,
    *,
    limit: int = DEFAULT_LARK_TEXT_LIMIT,
    preserve_format: bool = False,
    max_parts: int | None = None,
    overflow_note: str | None = None,
) -> list[str]:
    """Split an over-limit body into bounded, ordered, lossless parts.

    The provider rejects a reply over the delivery limit, which used to leave a
    persisted manager answer undelivered. Splitting happens after the same
    validation every other outbound write uses, packs whole lines while they
    fit, hard-splits a single long line, and marks each part with ``(i/N)`` so a
    reader can order the parts. The body itself is not reworded, abbreviated or
    re-flowed: removing the markers restores the validated text exactly.

    ``max_parts`` bounds how many messages an oversized answer may become. A
    caller that sets it must also pass ``overflow_note``: the note replaces the
    parts beyond the bound, so the reader learns the answer was longer instead
    of receiving a flood of messages (or nothing at all).
    """

    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        raise ValueError("limit must be a positive integer")
    if max_parts is not None and (
        not isinstance(max_parts, int) or isinstance(max_parts, bool) or max_parts < 1
    ):
        raise ValueError("max_parts must be a positive integer")
    if max_parts is not None and not (overflow_note or "").strip():
        raise ValueError("max_parts requires an overflow_note")
    normalized = normalize_lark_outbound_text(
        value, limit=None, preserve_format=preserve_format
    )
    if len(normalized) <= limit:
        return [normalized]
    budget = limit - LARK_PART_MARKER_BUDGET
    if budget <= 0:
        raise LarkOutboundTextError(
            f"delivery limit {limit} cannot hold a part marker"
        )
    pieces: list[str] = []
    buffer = ""
    for line in normalized.splitlines(keepends=True):
        while len(line) > budget:
            head, line = line[:budget], line[budget:]
            if buffer:
                pieces.append(buffer)
                buffer = ""
            pieces.append(head)
        if len(buffer) + len(line) <= budget:
            buffer += line
            continue
        if buffer:
            pieces.append(buffer)
        buffer = line
    if buffer:
        pieces.append(buffer)
    total = len(pieces)
    if max_parts is not None and total > max_parts:
        pieces = pieces[: max_parts - 1] + [str(overflow_note)]
        total = len(pieces)
    parts = [
        f"({index}/{total}) {piece}" for index, piece in enumerate(pieces, start=1)
    ]
    # A part that still cannot be delivered would fail the same way the whole
    # body did, so refuse locally instead of writing half an answer.
    for part in parts:
        normalize_lark_outbound_text(part, limit=limit, preserve_format=preserve_format)
    return parts


def safe_lark_plain_text_fallback(value: Any) -> str:
    """Downgrade presentation-only defects without granting mention authority.

    The strict outbound validator remains the primary path.  This fallback is
    intended for an already persisted manager answer: literal ``\\n`` tokens
    outside fenced code become real newlines, unsupported ``<at>`` fragments
    are made inert, and unresolved visual ``@`` mentions are rendered with a
    full-width marker.  Valid structured mentions are preserved for the normal
    membership verification performed by the reply transport.
    """

    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")

    def degrade_outside_code(fragment: str) -> str:
        degraded: list[str] = []
        cursor = 0
        for mention in AT_MENTION_PATTERN.finditer(fragment):
            plain = fragment[cursor : mention.start()].replace(r"\n", "\n")
            plain = re.sub(
                r"</?at\b",
                lambda match: match.group(0).replace("<", "‹"),
                plain,
                flags=re.IGNORECASE,
            )
            plain = LITERAL_MENTION_PATTERN.sub(
                lambda match: "＠" + match.group(0)[1:], plain
            )
            degraded.extend((plain, mention.group(0)))
            cursor = mention.end()
        plain = fragment[cursor:].replace(r"\n", "\n")
        plain = re.sub(
            r"</?at\b",
            lambda match: match.group(0).replace("<", "‹"),
            plain,
            flags=re.IGNORECASE,
        )
        plain = LITERAL_MENTION_PATTERN.sub(
            lambda match: "＠" + match.group(0)[1:], plain
        )
        degraded.append(plain)
        return "".join(degraded)

    chunks: list[str] = []
    cursor = 0
    for fenced in FENCED_CODE_PATTERN.finditer(text):
        chunks.append(degrade_outside_code(text[cursor : fenced.start()]))
        chunks.append(fenced.group(0))
        cursor = fenced.end()
    chunks.append(degrade_outside_code(text[cursor:]))
    return normalize_lark_outbound_text(
        "".join(chunks), limit=None, preserve_format=True
    )


def validate_lark_text_request_size(body: Mapping[str, Any]) -> None:
    size = len(json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > LARK_TEXT_REQUEST_MAX_BYTES:
        raise LarkOutboundTextError("Lark text request exceeds the 150 KB provider limit")


def lark_provider_preview_matches_outbound(
    *, outbound_text: str, payload: Mapping[str, Any]
) -> bool:
    data = payload.get("data")
    api_calls = payload.get("api")
    if not isinstance(api_calls, list) and isinstance(data, Mapping):
        api_calls = data.get("api")
    if not isinstance(api_calls, list):
        return False
    for call in api_calls:
        if not isinstance(call, Mapping):
            continue
        body = call.get("body")
        if not isinstance(body, Mapping):
            continue
        preview_text = _content_text(body)
        if preview_text and normalized_lark_lines(
            preview_text
        ) == normalized_lark_lines(outbound_text):
            return True
    return False


def lark_readback_matches_outbound(
    *,
    outbound_text: str,
    message: Mapping[str, Any],
    verified_bot_aliases: Mapping[str, str] | None = None,
) -> bool:
    expected_text, identity_tokens = _canonical_expected_text(outbound_text)
    actual_text = _message_text(message)
    if not actual_text:
        return False

    mentions = message.get("mentions")
    if not identity_tokens:
        return (mentions is None or mentions == []) and normalized_lark_lines(
            actual_text
        ) == expected_text
    if not isinstance(mentions, list):
        return False

    matched_identities: set[str] = set()
    keys_by_identity: dict[str, set[str]] = {}
    display_text_by_identity: dict[str, set[str]] = {}
    key_owners: dict[str, set[str]] = {}
    display_text_owners: dict[str, set[str]] = {}
    for mention in mentions:
        if not isinstance(mention, Mapping):
            return False
        key = str(mention.get("key") or "")
        identities = _mention_identities(mention)
        identities.update(
            (verified_bot_aliases or {})[identity]
            for identity in tuple(identities)
            if identity in (verified_bot_aliases or {})
        )
        matches = identities.intersection(identity_tokens)
        if not key or len(matches) != 1:
            return False
        identity = next(iter(matches))
        matched_identities.add(identity)
        keys_by_identity.setdefault(identity, set()).add(key)
        key_owners.setdefault(key, set()).add(identity)
        display_name = str(mention.get("name") or "").strip()
        if display_name:
            display_text = f"@{display_name}"
            display_text_by_identity.setdefault(identity, set()).add(display_text)
            display_text_owners.setdefault(display_text, set()).add(identity)
    if matched_identities != set(identity_tokens):
        return False
    if any(len(owners) != 1 for owners in key_owners.values()):
        return False

    for identity, token in identity_tokens.items():
        expected_count = expected_text.count(token)
        for key in sorted(keys_by_identity.get(identity, ()), key=len, reverse=True):
            actual_text = actual_text.replace(key, token)
        remaining_count = expected_count - actual_text.count(token)
        if remaining_count < 0:
            return False
        if remaining_count == 0:
            continue
        rendered_candidates = [
            display_text
            for display_text in display_text_by_identity.get(identity, ())
            if display_text_owners.get(display_text) == {identity}
            and actual_text.count(display_text) == remaining_count
        ]
        if len(rendered_candidates) != 1:
            return False
        actual_text = actual_text.replace(rendered_candidates[0], token)
    return normalized_lark_lines(actual_text) == expected_text


def lark_markdown_post_content(text: str) -> str:
    """Preserve authored Markdown without the CLI's image-fetch/rewrite pass."""
    return json.dumps({"zh_cn": {"content": [[{"tag": "md", "text": text}]]}},
                      ensure_ascii=False, separators=(",", ":"))


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
    return node["text"] if node["tag"] == "md" and isinstance(node["text"], str) else None


def lark_markdown_preview_matches(*, text: str, payload: Mapping[str, Any]) -> bool:
    data = payload.get("data")
    calls = payload.get("api")
    if calls is None and isinstance(data, Mapping):
        calls = data.get("api")
    if not isinstance(calls, list) or len(calls) != 1:
        return False
    call = calls[0]
    body = call.get("body") if isinstance(call, Mapping) else None
    return (isinstance(body, Mapping) and body.get("msg_type") == "post"
            and _single_markdown_post(body.get("content")) == text)


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
    return isinstance(actual, str) and actual.replace("\r\n", "\n").strip() == text
