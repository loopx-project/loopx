"""JS/TS reference lexing, static-key parsing and property-key classification.

Bounded reference analysis for anti-entropy. This module does not import the
oracle core, so it can be reviewed and tested independently of diff/evidence
orchestration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_TS_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*\Z")
_TS_QUOTE_CHARS = ("\"", "'", chr(96))
_HEX_DIGITS = frozenset("0123456789abcdefABCDEF")
_JS_SIMPLE_ESCAPES = {
    "\"": "\"",
    "'": "'",
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "v": "\v",
}
_JS_ESCAPE_HINT_RE = re.compile(
    r"\\(?:u\{?[0-9a-fA-F]|x[0-9a-fA-F]|[0-7]|[_$A-Za-z]|\r?\n)"
)
_JS_ESCAPE_SEQUENCE_RE = re.compile(
    r"\\(?:u\{[0-9a-fA-F]{1,6}\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[0-7]{1,3}"
    r"|[_$A-Za-z]|\r?\n)"
)
_TS_ESCAPED_IDENTIFIER_RE = re.compile(
    r"(?:[A-Za-z_$]|\\u\{[0-9a-fA-F]{1,6}\}|\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\[_$A-Za-z])"
    r"(?:[A-Za-z0-9_$]|\\u\{[0-9a-fA-F]{1,6}\}|\\u[0-9a-fA-F]{4}|\\x[0-9a-fA-F]{2}|\\[0-7]{1,3}|\\[_$A-Za-z])*"
)
_TS_MEMBER_KEYWORDS = frozenset(
    {
        "return",
        "throw",
        "case",
        "yield",
        "await",
        "typeof",
        "void",
        "delete",
        "new",
        "in",
        "instanceof",
        "export",
        "default",
        "const",
        "let",
        "var",
        "import",
    }
)


def _decode_js_escape_text(raw: str) -> str | None:
    """Decode a JS/TS quoted-string body, including identifier escapes.

    Returns ``None`` when the escape sequence is malformed or truncated. Callers
    treat that as insufficient evidence, never as an absent reference.
    """
    output: list[str] = []
    index = 0
    length = len(raw)
    while index < length:
        char = raw[index]
        if char != "\\":
            output.append(char)
            index += 1
            continue
        if index + 1 >= length:
            return None
        nxt = raw[index + 1]
        if nxt == "u":
            if index + 2 < length and raw[index + 2] == "{":
                end = raw.find("}", index + 3)
                if end == -1:
                    return None
                digits = raw[index + 3 : end]
                if not 1 <= len(digits) <= 6 or not set(digits) <= _HEX_DIGITS:
                    return None
                code = int(digits, 16)
                if code > 0x10FFFF or 0xD800 <= code <= 0xDFFF:
                    return None
                output.append(chr(code))
                index = end + 1
                continue
            digits = raw[index + 2 : index + 6]
            if len(digits) != 4 or not set(digits) <= _HEX_DIGITS:
                return None
            code = int(digits, 16)
            if 0xD800 <= code <= 0xDFFF:
                return None
            output.append(chr(code))
            index += 6
            continue
        if nxt == "x":
            digits = raw[index + 2 : index + 4]
            if len(digits) != 2 or not set(digits) <= _HEX_DIGITS:
                return None
            output.append(chr(int(digits, 16)))
            index += 4
            continue
        if nxt in "01234567":
            # Legacy octal escape (non-strict JS strings). A lone \0 is NUL;
            # \062 is one character "2", not NUL plus "62".
            if nxt == "0" and (
                index + 2 >= length or raw[index + 2] not in "01234567"
            ):
                output.append(chr(0))
                index += 2
                continue
            cursor = index + 1
            digits = ""
            max_digits = 3 if nxt in "0123" else 2
            while (
                cursor < length
                and len(digits) < max_digits
                and raw[cursor] in "01234567"
            ):
                digits += raw[cursor]
                cursor += 1
            code = int(digits, 8)
            if code > 0xFF:
                return None
            output.append(chr(code))
            index = cursor
            continue
        if nxt in _JS_SIMPLE_ESCAPES:
            output.append(_JS_SIMPLE_ESCAPES[nxt])
            index += 2
            continue
        if nxt == "\n":
            index += 2
            continue
        if nxt == "\r":
            index += 3 if index + 2 < length and raw[index + 2] == "\n" else 2
            continue
        # NonEscapeCharacter: the escaped character has no special meaning.
        output.append(nxt)
        index += 2
    return "".join(output)


def _decode_js_escapes_lenient(text: str) -> str:
    """Decode JS escapes where possible; leave malformed sequences unchanged."""

    def replace(match: re.Match[str]) -> str:
        decoded = _decode_js_escape_text(match.group(0))
        return match.group(0) if decoded is None else decoded

    return _JS_ESCAPE_SEQUENCE_RE.sub(replace, text)


_REGEX_ALLOWED_AFTER = frozenset("=([{,:;!&|?+-*%^~<>")
_REGEX_ALLOWED_AFTER_WORDS = frozenset(
    {
        "return",
        "throw",
        "case",
        "typeof",
        "delete",
        "void",
        "new",
        "in",
        "instanceof",
        "yield",
        "await",
        "do",
        "else",
    }
)


def _regex_starts_at(code: str, index: int) -> bool:
    """Heuristic: does a slash at ``index`` start a regex literal, not division?"""
    prefix = code[:index].rstrip()
    previous_char = prefix[-1] if prefix else ""
    word_match = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)$", prefix)
    previous_word = word_match.group(1) if word_match else ""
    return (
        not prefix
        or previous_char in _REGEX_ALLOWED_AFTER
        or previous_word in _REGEX_ALLOWED_AFTER_WORDS
    )


def _regex_literal_spans(code: str) -> list[tuple[int, int]]:
    """Return heuristic regex-literal spans in stripped code.

    Only used to keep identifier-escape normalization out of pattern text; a
    slash after an identifier, literal, ``)`` or ``]`` is division and stays code.
    """
    spans: list[tuple[int, int]] = []
    index = 0
    length = len(code)
    while index < length:
        if code[index] != "/":
            index += 1
            continue
        if not _regex_starts_at(code, index):
            index += 1
            continue
        cursor = index + 1
        in_class = False
        closed = False
        while cursor < length:
            char = code[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "\n":
                break
            if char == "[":
                in_class = True
            elif char == "]":
                in_class = False
            elif char == "/" and not in_class:
                closed = True
                break
            cursor += 1
        if not closed:
            index += 1
            continue
        spans.append((index, cursor + 1))
        index = cursor + 1
    return spans


def _normalize_identifier_escapes(code: str) -> str:
    """Decode escapes embedded in code identifiers so ``old\\u005fapi`` is visible."""
    spans = _regex_literal_spans(code)

    def replace(match: re.Match[str]) -> str:
        token = match.group(0)
        if "\\" not in token:
            return token
        if any(start <= match.start() < end for start, end in spans):
            return token
        decoded = _decode_js_escape_text(token)
        return token if decoded is None else decoded

    return _TS_ESCAPED_IDENTIFIER_RE.sub(replace, code)


@dataclass(frozen=True, slots=True)
class _ComputedMember:
    """Result of parsing a static computed member access in value position."""

    name: str
    next_index: int
    resolved: bool
    newlines: int = 0


@dataclass(frozen=True, slots=True)
class _StaticKey:
    """A statically evaluated computed-member key expression."""

    value: str
    next_index: int
    resolved: bool


_JS_EXTRA_WHITESPACE = "\u00a0\u2028\u2029\ufeff"
_TS_ASSERTION_KEYWORDS = ("as", "satisfies")
_TS_IDENTIFIER_AT_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")


def _is_js_whitespace(char: str) -> bool:
    return char.isspace() or char in _JS_EXTRA_WHITESPACE


def _skip_js_trivia(text: str, index: int) -> tuple[int, bool]:
    """Skip JS whitespace and comments between tokens.

    Returns ``(index, True)`` on success and ``(index, False)`` for an unclosed
    block comment; callers fail closed in that case.
    """
    length = len(text)
    while index < length:
        char = text[index]
        if _is_js_whitespace(char):
            index += 1
            continue
        if text.startswith("//", index):
            cursor = index + 2
            while cursor < length and text[cursor] not in "\r\n\u2028\u2029":
                cursor += 1
            index = cursor
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close == -1:
                return index, False
            index = close + 2
            continue
        break
    return index, True


def _previous_receiver_char(out: list[str]) -> str:
    """Return the code character before the member bracket, skipping ``!`` suffix."""
    for position in range(len(out) - 1, -1, -1):
        for char in reversed(out[position]):
            if char.isspace() or char == "!":
                continue
            return char
    return ""


def _previous_receiver_word_info(out: list[str]) -> tuple[str, str]:
    """Return the identifier before the member bracket and its prefix text."""
    code = "".join(out)
    end = len(code)
    while end and (code[end - 1].isspace() or code[end - 1] == "!"):
        end -= 1
    start = end
    while start and (code[start - 1].isalnum() or code[start - 1] in "_$"):
        start -= 1
    return code[start:end], code[:start]


_TS_CONTEXTUAL_MEMBER_WORDS = frozenset({"of"})


def _is_contextual_member_keyword(prefix: str, word: str) -> bool:
    """Distinguish the ``for (... of ...)`` keyword from an identifier named ``of``."""
    if word != "of":
        return False
    # ``for (const k of [ ... ])``: the current word is the first top-level
    # ``of`` after a binding, not a later variable expression.
    return (
        re.search(
            r"\bfor\s*\(\s*(?:(?:const|let|var)\s+)?"
            r"[A-Za-z_$][A-Za-z0-9_$]*\s*$",
            prefix,
            re.S,
        )
        is not None
    )


def _parse_static_string(text: str, index: int) -> _StaticKey:
    quote = text[index]
    length = len(text)
    cursor = index + 1
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            if cursor + 1 >= length:
                return _StaticKey("", length, False)
            cursor += 2
            continue
        if char == quote:
            decoded = _decode_js_escape_text(text[index + 1 : cursor])
            if decoded is None:
                return _StaticKey("", cursor + 1, False)
            return _StaticKey(decoded, cursor + 1, True)
        if char in "\r\n\u2028\u2029":
            return _StaticKey("", cursor, False)
        cursor += 1
    return _StaticKey("", length, False)


def _parse_static_template(
    text: str, index: int, *, raw_mode: bool = False
) -> _StaticKey | None:
    length = len(text)
    cursor = index + 1
    literal_start = cursor
    pieces: list[str] = []

    def append_literal(end: int) -> _StaticKey | None:
        segment = text[literal_start:end]
        if raw_mode:
            pieces.append(segment)
            return None
        decoded = _decode_js_escape_text(segment)
        if decoded is None:
            return _StaticKey("", end, False)
        pieces.append(decoded)
        return None

    while cursor < length:
        char = text[cursor]
        if char == "\\":
            if cursor + 1 >= length:
                return _StaticKey("", length, False)
            cursor += 2
            continue
        if char == "`":
            failure = append_literal(cursor)
            if failure is not None:
                return failure
            return _StaticKey("".join(pieces), cursor + 1, True)
        if char == "$" and cursor + 1 < length and text[cursor + 1] == "{":
            failure = append_literal(cursor)
            if failure is not None:
                return failure
            inner = _parse_static_key_expression(text, cursor + 2)
            if inner is None:
                return None
            if not inner.resolved:
                return _StaticKey("", inner.next_index, False)
            close, ok = _skip_js_trivia(text, inner.next_index)
            if not ok:
                return _StaticKey("", close, False)
            if close >= length or text[close] != "}":
                return None
            pieces.append(inner.value)
            cursor = close + 1
            literal_start = cursor
            continue
        if char in "\r\n":
            cursor += 1
            continue
        cursor += 1
    return _StaticKey("", length, False)


def _skip_ts_assertions(text: str, index: int) -> tuple[int, bool]:
    """Skip simple TS type assertions/postfixes in a key expression."""
    length = len(text)
    while True:
        index, ok = _skip_js_trivia(text, index)
        if not ok:
            return index, False
        match = _TS_IDENTIFIER_AT_RE.match(text, index)
        if match is not None and match.group(0) in _TS_ASSERTION_KEYWORDS:
            index = match.end()
            index, ok = _skip_js_trivia(text, index)
            if not ok:
                return index, False
            type_match = _TS_IDENTIFIER_AT_RE.match(text, index)
            if type_match is None:
                return index, False
            index = type_match.end()
            # ``as string[]`` / ``as T[][]``: empty array suffixes keep the value.
            while index + 1 < length and text[index] == "[" and text[index + 1] == "]":
                index += 2
            continue
        if index < length and text[index] == "!":
            index += 1
            continue
        return index, True


def _skip_call_tail(text: str, index: int) -> int | None:
    """Skip a call argument tail to the closing ``)`` (strings/comments aware)."""
    length = len(text)
    depth = 1
    while index < length:
        char = text[index]
        if char in ('"', "'", "`"):
            quote = char
            index += 1
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        if text.startswith("//", index):
            while index < length and text[index] not in "\r\n\u2028\u2029":
                index += 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            if close == -1:
                return None
            index = close + 2
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def _parse_string_call(text: str, index: int) -> _StaticKey | None:
    length = len(text)
    index, ok = _skip_js_trivia(text, index)
    if not ok:
        return _StaticKey("", index, False)
    if index < length and text[index] == ".":
        index, ok = _skip_js_trivia(text, index + 1)
        if not ok:
            return _StaticKey("", index, False)
        if not text.startswith("raw", index):
            return None
        index += 3
        index, ok = _skip_js_trivia(text, index)
        if not ok:
            return _StaticKey("", index, False)
        if index < length and text[index] == "`":
            return _parse_static_template(text, index, raw_mode=True)
        return None
    if index < length and text[index] == "(":
        inner = _parse_static_key_expression(text, index + 1)
        if inner is None:
            return None
        if not inner.resolved:
            return _StaticKey("", inner.next_index, False)
        close, ok = _skip_js_trivia(text, inner.next_index)
        if not ok:
            return _StaticKey("", close, False)
        if close < length and text[close] == ")":
            return _StaticKey(inner.value, close + 1, True)
        if close < length and text[close] == ",":
            tail = _skip_call_tail(text, close + 1)
            if tail is not None:
                return _StaticKey(inner.value, tail, True)
    return None


def _parse_static_primary(text: str, index: int) -> _StaticKey | None:
    index, ok = _skip_js_trivia(text, index)
    if not ok:
        return _StaticKey("", index, False)
    if index >= len(text):
        return None
    char = text[index]
    if char == "`":
        return _parse_static_template(text, index)
    if char in _TS_QUOTE_CHARS:
        return _parse_static_string(text, index)
    if char == "(":
        inner = _parse_static_key_expression(text, index + 1)
        if inner is None:
            return None
        if not inner.resolved:
            return _StaticKey("", inner.next_index, False)
        close, ok = _skip_js_trivia(text, inner.next_index)
        if not ok:
            return _StaticKey("", close, False)
        if close < len(text) and text[close] == ")":
            return _StaticKey(inner.value, close + 1, inner.resolved)
        return _StaticKey("", close, False)
    if text.startswith("String", index):
        after = index + 6
        if after >= len(text) or not (
            text[after].isalnum() or text[after] in "_$"
        ):
            return _parse_string_call(text, after)
    return None


def _parse_static_key_expression(text: str, index: int) -> _StaticKey | None:
    first = _parse_static_primary(text, index)
    if first is None:
        return None
    if not first.resolved:
        return first
    value = first.value
    cursor = first.next_index
    while True:
        cursor, ok = _skip_ts_assertions(text, cursor)
        if not ok:
            return _StaticKey("", cursor, False)
        cursor, ok = _skip_js_trivia(text, cursor)
        if not ok:
            return _StaticKey("", cursor, False)
        if cursor < len(text) and text[cursor] == "+":
            right = _parse_static_primary(text, cursor + 1)
            if right is None:
                return None
            if not right.resolved:
                return _StaticKey("", right.next_index, False)
            value += right.value
            cursor = right.next_index
            continue
        return _StaticKey(value, cursor, True)


def _static_computed_member(
    text: str, start: int, out: list[str]
) -> _ComputedMember | None:
    """Preserve a static computed member access (api[key]) in value position.

    The key is parsed as a bounded static expression: string/template literals,
    parenthesized static keys, constant ``+`` concatenation, ``String(...)``
    and simple TypeScript assertions. Any legal JS trivia (whitespace, line
    terminators, comments) is skipped at every token boundary. A malformed
    static form is reported as unresolved so the tokenizer fails closed with
    AE004; a dynamic expression keeps the documented dynamic-key limitation.
    """
    length = len(text)
    index, ok = _skip_js_trivia(text, start + 1)
    if not ok:
        return _ComputedMember("", length, False)
    key = _parse_static_key_expression(text, index)
    if key is None:
        return None
    if not key.resolved:
        return _ComputedMember("", key.next_index, False)
    after, ok = _skip_js_trivia(text, key.next_index)
    if not ok:
        return _ComputedMember("", length, False)
    if after >= length or text[after] != "]":
        return None
    previous = _previous_receiver_char(out)
    if not (previous.isalnum() or previous in "_$)]."):
        return None
    previous_word, prefix = _previous_receiver_word_info(out)
    if previous_word in _TS_MEMBER_KEYWORDS:
        return None
    if previous_word in _TS_CONTEXTUAL_MEMBER_WORDS and _is_contextual_member_keyword(
        prefix, previous_word
    ):
        return None
    next_index = after + 1
    name = key.value if _TS_IDENTIFIER_RE.match(key.value) else ""
    return _ComputedMember(
        name,
        next_index,
        True,
        text.count("\n", start, next_index),
    )



@dataclass(slots=True)
class _JsBracketGroup:
    start: int
    open_char: str
    parent: int | None = None
    end: int = -1
    kind: str = "unknown"


def _skip_js_string_literal(text: str, index: int) -> int:
    quote = text[index]
    length = len(text)
    cursor = index + 1
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == quote:
            return cursor + 1
        if quote != "`" and char in "\r\n\u2028\u2029":
            return cursor
        cursor += 1
    return length


def _scan_js_bracket_groups(text: str) -> list[_JsBracketGroup]:
    """Collect balanced bracket groups with parent links, skipping JS strings/comments."""
    groups: list[_JsBracketGroup] = []
    stack: list[int] = []
    index = 0
    length = len(text)
    closing = {"}": "{", "]": "[", ")": "("}
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            newline = index + 2
            while newline < length and text[newline] not in "\r\n\u2028\u2029":
                newline += 1
            index = newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close == -1 else close + 2
            continue
        if char in _TS_QUOTE_CHARS:
            index = _skip_js_string_literal(text, index)
            continue
        if char in "{[(":
            group = _JsBracketGroup(
                start=index,
                open_char=char,
                parent=stack[-1] if stack else None,
            )
            groups.append(group)
            stack.append(len(groups) - 1)
            index += 1
            continue
        if char in closing:
            if stack:
                top = groups[stack[-1]]
                if top.open_char == closing[char]:
                    top.end = index
                    stack.pop()
            index += 1
            continue
        index += 1
    return groups


def _token_after(text: str, index: int) -> tuple[str, str]:
    index, ok = _skip_js_trivia(text, index)
    if not ok or index >= len(text):
        return "", ""
    char = text[index]
    if char.isalpha() or char in "_$":
        match = _TS_IDENTIFIER_AT_RE.match(text, index)
        if match is not None:
            return "", match.group(0)
    return char, ""


def _token_before(text: str, index: int) -> tuple[str, str]:
    cursor = index
    while True:
        while cursor > 0 and text[cursor - 1].isspace():
            cursor -= 1
        if cursor >= 2 and text[cursor - 2 : cursor] == "*/":
            open_comment = text.rfind("/*", 0, cursor - 1)
            cursor = 0 if open_comment == -1 else open_comment
            continue
        line_start = text.rfind("\n", 0, cursor) + 1
        line_comment = text.rfind("//", line_start, cursor)
        if line_comment != -1:
            cursor = line_comment
            continue
        break
    if cursor <= 0:
        return "", ""
    end = cursor
    while cursor > 0 and (text[cursor - 1].isalnum() or text[cursor - 1] in "_$"):
        cursor -= 1
    if cursor < end:
        return "", text[cursor:end]
    return text[end - 1], ""


_TS_CONTROL_PAREN_WORDS = frozenset({"if", "while", "switch", "with"})
_TS_EXPRESSION_LITERAL_LEADS = frozenset("=([,:?!&|+-*/%^~<>")


def _classify_js_group(
    groups: list[_JsBracketGroup], group_index: int, text: str
) -> str:
    group = groups[group_index]
    if group.kind != "unknown":
        return group.kind
    before_char, before_word = _token_before(text, group.start)
    after_char, after_word = _token_after(text, group.end + 1 if group.end >= 0 else len(text))
    parent_kind = (
        groups[group.parent].kind
        if group.parent is not None
        else "unknown"
    )
    if parent_kind == "unknown" and group.parent is not None:
        parent_kind = _classify_js_group(groups, group.parent, text)
    if group.open_char == "(":
        after_index, after_ok = _skip_js_trivia(
            text, group.end + 1 if group.end >= 0 else len(text)
        )
        is_arrow_params = after_ok and text.startswith("=>", after_index)
        if before_word == "for":
            kind = "for_header"
        elif before_word == "catch":
            kind = "catch"
        elif before_word in _TS_CONTROL_PAREN_WORDS:
            kind = "expression"
        elif is_arrow_params:
            kind = "params"
        elif after_char in ("{", ":") and before_word not in ("", "return"):
            kind = "params"
        else:
            kind = "expression"
    elif group.open_char in "{[":
        if before_word in ("const", "let", "var"):
            kind = "pattern"
        elif before_char == "(" and parent_kind in ("params", "catch", "for_header"):
            kind = "pattern"
        elif after_char == "=":
            kind = "pattern"
        elif after_word in ("of", "in"):
            kind = "pattern"
        elif parent_kind == "pattern":
            if before_char == "=":
                kind = "literal"
            else:
                kind = "pattern"
        elif parent_kind == "literal":
            kind = "literal"
        elif before_char in _TS_EXPRESSION_LITERAL_LEADS or before_word in (
            "return",
            "throw",
            "case",
            "yield",
            "await",
            "typeof",
            "void",
            "delete",
            "new",
            "instanceof",
            "of",
            "in",
            "default",
        ):
            kind = "literal"
        else:
            kind = "ambiguous"
    else:
        kind = "ambiguous"
    group.kind = kind
    return kind


def _enclosing_object_group(
    groups: list[_JsBracketGroup], index: int
) -> _JsBracketGroup | None:
    best: _JsBracketGroup | None = None
    for group in groups:
        if (
            group.open_char == "{"
            and group.end > index
            and group.start < index
            and (best is None or group.start > best.start)
        ):
            best = group
    return best


def _extract_module_string_name_refs(
    text: str, target_symbols: set[str]
) -> set[str]:
    """Collect static string names in import/export clauses.

    ``import { 'old_api' as fn } from './service'`` and re-exports name the
    imported binding with a string literal; the name is an executable reference
    even though it is not a property-key token.
    """
    refs: set[str] = set()
    index = 0
    length = len(text)
    while index < length:
        if text.startswith("//", index):
            index += 2
            while index < length and text[index] not in "\r\n\u2028\u2029":
                index += 1
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close == -1 else close + 2
            continue
        if text[index] in _TS_QUOTE_CHARS:
            index = _skip_js_string_literal(text, index)
            continue
        keyword = None
        for candidate in ("import", "export"):
            if text.startswith(candidate, index):
                after = index + len(candidate)
                if after >= length or not (
                    text[after].isalnum() or text[after] in "_$"
                ):
                    keyword = candidate
                    break
        if keyword is None:
            index += 1
            continue
        clause_start, ok = _skip_js_trivia(text, index + len(keyword))
        if not ok or clause_start >= length or text[clause_start] != "{":
            index += len(keyword)
            continue
        cursor = clause_start + 1
        depth = 1
        while cursor < length and depth:
            char = text[cursor]
            if char in _TS_QUOTE_CHARS:
                parsed = (
                    _parse_static_template(text, cursor)
                    if char == "`"
                    else _parse_static_string(text, cursor)
                )
                if parsed is not None and parsed.resolved and parsed.value in target_symbols:
                    refs.add(parsed.value)
                cursor = _skip_js_string_literal(text, cursor)
                continue
            if text.startswith("//", cursor):
                cursor += 2
                while cursor < length and text[cursor] not in "\r\n\u2028\u2029":
                    cursor += 1
                continue
            if text.startswith("/*", cursor):
                close = text.find("*/", cursor + 2)
                cursor = length if close == -1 else close + 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        index = cursor
    return refs


def _find_template_expression_end(text: str, start: int) -> int:
    """Return the closing brace index for a template ``${...}`` expression."""
    cursor = start
    depth = 1
    length = len(text)
    while cursor < length:
        char = text[cursor]
        if char in _TS_QUOTE_CHARS:
            cursor = _skip_js_string_literal(text, cursor)
            continue
        if text.startswith("//", cursor):
            cursor += 2
            while cursor < length and text[cursor] not in "\r\n\u2028\u2029":
                cursor += 1
            continue
        if text.startswith("/*", cursor):
            close = text.find("*/", cursor + 2)
            cursor = length if close == -1 else close + 2
            continue
        if char == "/" and _regex_starts_at(text, cursor):
            regex_cursor = cursor + 1
            in_class = False
            closed = False
            while regex_cursor < length:
                regex_char = text[regex_cursor]
                if regex_char == "\\":
                    regex_cursor += 2
                    continue
                if regex_char in "\r\n\u2028\u2029":
                    break
                if regex_char == "[":
                    in_class = True
                elif regex_char == "]":
                    in_class = False
                elif regex_char == "/" and not in_class:
                    closed = True
                    break
                regex_cursor += 1
            if not closed:
                return -1
            cursor = regex_cursor + 1
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return -1


def _template_interpolation_ranges(
    text: str, index: int
) -> tuple[list[tuple[int, int]], int]:
    """Collect executable ``${...}`` ranges in a template literal.

    Returns the expression ranges and the index just past the closing backtick.
    Interpolation bodies are scanned independently so destructuring reads inside
    them cannot be erased when the template is treated as a string value.
    """
    ranges: list[tuple[int, int]] = []
    cursor = index + 1
    length = len(text)
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "`":
            return ranges, cursor + 1
        if text.startswith("${", cursor):
            expression_start = cursor + 2
            expression_end = _find_template_expression_end(text, expression_start)
            if expression_end == -1:
                return ranges, length
            ranges.append((expression_start, expression_end))
            cursor = expression_end + 1
            continue
        cursor += 1
    return ranges, length


def _extract_static_property_key_refs(
    text: str, target_symbols: set[str]
) -> tuple[set[str], str]:
    """Extract static property-key references and flag ambiguous executable contexts.

    Clear destructuring/binding/parameter/assignment/for-of property keys are
    references. Clear object-literal property keys are not. A static target key
    in a context the bounded classifier cannot classify makes the file's source
    evidence incomplete (AE004); dynamic destructuring keys do the same because
    they are executable property reads we cannot resolve.
    """
    if not target_symbols:
        return set(), ""
    groups = _scan_js_bracket_groups(text)
    refs: set[str] = set()
    incomplete_reason = ""
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if text.startswith("//", index):
            newline = index + 2
            while newline < length and text[newline] not in "\r\n\u2028\u2029":
                newline += 1
            index = newline
            continue
        if text.startswith("/*", index):
            close = text.find("*/", index + 2)
            index = length if close == -1 else close + 2
            continue
        if char in _TS_QUOTE_CHARS:
            parsed = (
                _parse_static_template(text, index)
                if char == "`"
                else _parse_static_string(text, index)
            )
            if parsed is None:
                # Dynamic template: an executable static property read cannot be
                # resolved; only relevant when it is a pattern property key.
                interpolation_ranges, next_index = _template_interpolation_ranges(
                    text, index
                )
                group = _enclosing_object_group(groups, index)
                if group is not None:
                    kind = _classify_js_group(groups, groups.index(group), text)
                    previous, _ = _token_before(text, index)
                    after, _ = _token_after(text, next_index)
                    if previous in "{," and after == ":":
                        if kind == "pattern":
                            incomplete_reason = (
                                "a dynamic destructuring property key cannot be resolved"
                            )
                        elif kind == "ambiguous":
                            incomplete_reason = (
                                "a static property key cannot be classified as object literal or destructuring read"
                            )
                for expression_start, expression_end in interpolation_ranges:
                    inner_refs, inner_reason = _extract_static_property_key_refs(
                        text[expression_start:expression_end], target_symbols
                    )
                    refs.update(inner_refs)
                    if inner_reason and not incomplete_reason:
                        incomplete_reason = inner_reason
                index = next_index
                continue
            next_index = parsed.next_index
            group = _enclosing_object_group(groups, index)
            if group is not None:
                kind = _classify_js_group(groups, groups.index(group), text)
                previous, _ = _token_before(text, index)
                after, _ = _token_after(text, next_index)
                if previous in "{," and after == ":":
                    is_target = parsed.resolved and parsed.value in target_symbols
                    if kind == "pattern" and is_target:
                        refs.add(parsed.value)
                    elif kind == "ambiguous" and is_target:
                        incomplete_reason = (
                            "a static property key cannot be classified as object literal or destructuring read"
                        )
                    elif kind == "pattern" and not parsed.resolved:
                        incomplete_reason = (
                            "a malformed static destructuring property key cannot be resolved"
                        )
            index = next_index
            continue
        if char == "[":
            group = _enclosing_object_group(groups, index)
            if group is not None:
                kind = _classify_js_group(groups, groups.index(group), text)
                previous, _ = _token_before(text, index)
                if previous in "{,":
                    parsed = _parse_static_key_expression(text, index + 1)
                    if parsed is None or not parsed.resolved:
                        if kind in ("pattern", "ambiguous"):
                            incomplete_reason = (
                                "a computed destructuring property key cannot be resolved"
                            )
                        index += 1
                        continue
                    close, ok = _skip_js_trivia(text, parsed.next_index)
                    if ok and close < length and text[close] == "]":
                        after, _ = _token_after(text, close + 1)
                        if after == ":":
                            if kind == "pattern" and parsed.value in target_symbols:
                                refs.add(parsed.value)
                            elif kind == "ambiguous" and parsed.value in target_symbols:
                                incomplete_reason = (
                                    "a static property key cannot be classified as object literal or destructuring read"
                                )
                            index = close + 1
                            continue
                    index += 1
                    continue
            index += 1
            continue
        index += 1
    return refs, incomplete_reason


def _has_static_member_hint(content: str) -> bool:
    """True when a file contains a bracket followed by a static-key start.

    Uses the same JS trivia skipper as the parser so non-ASCII whitespace and
    line terminators (U+FEFF, U+2028/U+2029) cannot hide a composed key before
    the candidate gate runs.
    """
    index = content.find("[")
    while index != -1:
        after, ok = _skip_js_trivia(content, index + 1)
        if ok and after < len(content):
            char = content[after]
            if char in _TS_QUOTE_CHARS or char == "(":
                return True
            if content.startswith("String", after):
                boundary = after + 6
                if boundary >= len(content) or not (
                    content[boundary].isalnum() or content[boundary] in "_$"
                ):
                    return True
        index = content.find("[", index + 1)
    return False


def _has_property_key_hint(content: str) -> bool:
    """True for an object/pattern property key start after ``{``.

    Besides static keys this catches computed property keys such as
    ``const { [key]: fn } = api`` that have no literal target in the source but
    still perform an executable property read requiring completeness handling.
    """
    index = content.find("{")
    while index != -1:
        after, ok = _skip_js_trivia(content, index + 1)
        if ok and after < len(content) and content[after] == "[":
            return True
        index = content.find("{", index + 1)
    return False
