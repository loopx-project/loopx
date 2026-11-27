"""Bounded JSON slice validation and digest helpers for Codex native replay."""
from __future__ import annotations

import codecs
from collections.abc import Mapping
import hashlib
import json
import re
from typing import BinaryIO, NoReturn, cast

_IO_CHUNK_BYTES = 64 * 1024
# Bound one decoded protocol record, not the history or the replay interval.
_MAX_RECORD_BYTES = 16 * 1024 * 1024
_MAX_HEADER_FIELD_BYTES = 16 * 1024
_MAX_PRIMITIVE_BYTES = 1024
_MAX_SKIP_DEPTH = 256

_JSON_WS_STOP = re.compile(rb"[^ \t\r\n]")
_JSON_STR_SPAN = re.compile(
    rb'(?:[^"\\\x00-\x1f]+|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4})){1,64}'
)
_JSON_DIGITS = re.compile(rb"[0-9]+")
_JSON_PRIMITIVE_STOP = frozenset(b" \t\r\n,}]")
_JSON_TOKEN_STOP = re.compile(rb"[ \t\r\n,\}\]]")

_PREFIX_PAYLOAD_SPEC: dict[str, object] = {
    "type": None,
    "call_id": None,
    "name": None,
    "turn_id": None,
    "item": {"type": None, "id": None, "exit_code": None},
    "internal_chat_message_metadata_passthrough": {"turn_id": None},
}
_PREFIX_RECORD_SPEC: dict[str, object] = {"type": None, "payload": _PREFIX_PAYLOAD_SPEC}

JsonScalar = str | int | float | bool | None
JsonSmallValue = JsonScalar | dict[object, object] | list[object]


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


def _reject_json_constant(token: str) -> NoReturn:
    raise ValueError(f"unsupported JSON constant: {token}")


class _JsonSliceCursor:
    """Chunked JSON validator and header extractor for oversized Turn-prefix records."""

    def __init__(self, stream: BinaryIO, start: int, stop: int) -> None:
        stream.seek(start)
        self._stream = stream
        self._cursor = start
        self._stop = stop
        self._buf = b""
        self._pos = 0

    def _ensure(self, n: int = 1) -> bool:
        while len(self._buf) - self._pos < n and self._cursor < self._stop:
            chunk = self._stream.read(min(_IO_CHUNK_BYTES, self._stop - self._cursor))
            if not chunk:
                raise ValueError("native reader interval was truncated")
            self._cursor += len(chunk)
            self._buf = self._buf[self._pos :] + chunk
            self._pos = 0
        return len(self._buf) - self._pos >= n

    def _peek_byte(self) -> int | None:
        if self._pos >= len(self._buf) and not self._ensure(1):
            return None
        return self._buf[self._pos]

    def _consume_digits(self) -> bool:
        matched = False
        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                return matched
            match = _JSON_DIGITS.match(self._buf, self._pos)
            if match is None:
                return matched
            matched = True
            self._pos = match.end()

    def skip_ws(self) -> int | None:
        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                return None
            match = _JSON_WS_STOP.search(self._buf, self._pos)
            if match is not None:
                self._pos = match.start()
                return self._buf[self._pos]
            self._pos = len(self._buf)

    def consume_byte(self, expected: int) -> None:
        if not self._ensure(1) or self._buf[self._pos] != expected:
            raise ValueError("malformed native JSON record")
        self._pos += 1

    def scan_string(
        self, *, capture_limit: int | None = None, allow_long_key: bool = False
    ) -> str:
        self.consume_byte(0x22)
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        raw: bytearray | None = bytearray(b'"') if capture_limit is not None else None

        def _append_raw(data: bytes) -> bytearray | None:
            if raw is None or capture_limit is None:
                return None
            if len(raw) + len(data) > capture_limit:
                if not allow_long_key:
                    raise ValueError("oversized JSON header field")
                return None
            raw.extend(data)
            return raw

        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                raise ValueError("unterminated JSON string")
            match = _JSON_STR_SPAN.match(self._buf, self._pos)
            if match is not None:
                seg = self._buf[self._pos : match.end()]
                decoder.decode(seg, final=False)
                raw = _append_raw(seg)
                self._pos = match.end()
                continue
            ch = self._buf[self._pos]
            if ch == 0x22:
                decoder.decode(b"", final=True)
                self._pos += 1
                raw = _append_raw(b'"')
                return "" if raw is None else str(json.loads(bytes(raw)))
            if ch == 0x5C and len(self._buf) - self._pos < 6 and self._cursor < self._stop:
                before = len(self._buf) - self._pos
                self._ensure(6)
                if len(self._buf) - self._pos > before:
                    continue
            raise ValueError("invalid JSON string")

    def read_primitive(self) -> JsonScalar:
        raw = bytearray()
        while True:
            if self._pos >= len(self._buf) and not self._ensure(1):
                break
            match = _JSON_TOKEN_STOP.search(self._buf, self._pos)
            end = len(self._buf) if match is None else match.start()
            seg = self._buf[self._pos : end]
            if len(raw) + len(seg) > _MAX_PRIMITIVE_BYTES:
                raise ValueError("oversized JSON primitive")
            raw.extend(seg)
            self._pos = end
            if match is not None:
                break
        if not raw:
            raise ValueError("expected JSON value")
        return cast(
            JsonScalar,
            json.loads(bytes(raw), parse_constant=_reject_json_constant),
        )

    def skip_primitive(self) -> None:
        """Validate and skip a JSON literal or number in O(1) memory."""
        ch = self._peek_byte()
        if ch is None:
            raise ValueError("expected JSON value")
        if ch in {0x74, 0x66, 0x6E}:
            literal = b"true" if ch == 0x74 else b"false" if ch == 0x66 else b"null"
            if (
                not self._ensure(len(literal))
                or self._buf[self._pos : self._pos + len(literal)] != literal
            ):
                raise ValueError("invalid JSON literal")
            self._pos += len(literal)
        else:
            if ch == 0x2D:
                self._pos += 1
                ch = self._peek_byte()
                if ch is None:
                    raise ValueError("invalid JSON number")
            if ch == 0x30:
                self._pos += 1
            elif 0x31 <= ch <= 0x39:
                self._pos += 1
                self._consume_digits()
            else:
                raise ValueError("invalid JSON number")
            if self._peek_byte() == 0x2E:
                self._pos += 1
                if not self._consume_digits():
                    raise ValueError("invalid JSON number")
            if self._peek_byte() in {0x65, 0x45}:
                self._pos += 1
                if self._peek_byte() in {0x2B, 0x2D}:
                    self._pos += 1
                if not self._consume_digits():
                    raise ValueError("invalid JSON number")
        nxt = self._peek_byte()
        if nxt is not None and nxt not in _JSON_PRIMITIVE_STOP:
            raise ValueError("invalid JSON primitive")

    def skip_value(self) -> None:
        stack: list[bool] = []  # True = object, False = array
        state = 0  # 0=val, 1=val|arr_end, 2=arr_sep, 3=key|obj_end, 4=key, 5=colon, 6=obj_sep
        while True:
            ch = self.skip_ws()
            if ch is None:
                raise ValueError("unexpected EOF in JSON value")
            if state in {3, 6}:
                if ch == 0x7D:
                    self._pos += 1
                    stack.pop()
                    if not stack:
                        return
                    state = 6 if stack[-1] else 2
                    continue
                if state == 6:
                    if ch != 0x2C:
                        raise ValueError("expected comma or end of JSON object")
                    self._pos += 1
                    state = 4
                    continue
                state = 4
            if state == 4:
                self.scan_string()
                state = 5
                continue
            if state == 5:
                if ch != 0x3A:
                    raise ValueError("expected colon in JSON object")
                self._pos += 1
                state = 0
                continue
            if state == 2:
                if ch == 0x5D:
                    self._pos += 1
                    stack.pop()
                    if not stack:
                        return
                    state = 6 if stack[-1] else 2
                    continue
                if ch != 0x2C:
                    raise ValueError("expected comma or end of JSON array")
                self._pos += 1
                state = 0
                continue
            if state == 1 and ch == 0x5D:
                self._pos += 1
                stack.pop()
                if not stack:
                    return
                state = 6 if stack[-1] else 2
                continue
            if ch == 0x7B:
                if len(stack) >= _MAX_SKIP_DEPTH:
                    raise RecursionError("JSON nesting too deep")
                self._pos += 1
                stack.append(True)
                state = 3
            elif ch == 0x5B:
                if len(stack) >= _MAX_SKIP_DEPTH:
                    raise RecursionError("JSON nesting too deep")
                self._pos += 1
                stack.append(False)
                state = 1
            elif ch == 0x22:
                self.scan_string()
                if not stack:
                    return
                state = 6 if stack[-1] else 2
            else:
                self.skip_primitive()
                if not stack:
                    return
                state = 6 if stack[-1] else 2

    def read_small_value(self) -> JsonSmallValue:
        ch = self.skip_ws()
        if ch is None:
            raise ValueError("unexpected EOF in JSON value")
        if ch == 0x22:
            return self.scan_string(capture_limit=_MAX_HEADER_FIELD_BYTES)
        if ch == 0x7B:
            self.skip_value()
            return {}
        if ch == 0x5B:
            self.skip_value()
            return []
        return self.read_primitive()


def _scan_selected_object(
    cursor: _JsonSliceCursor, spec: Mapping[str, object]
) -> dict[str, object]:
    cursor.consume_byte(0x7B)
    result: dict[str, object] = {}
    ch = cursor.skip_ws()
    if ch == 0x7D:
        cursor._pos += 1
        return result
    while True:
        if ch != 0x22:
            raise ValueError("expected JSON object key")
        key = cursor.scan_string(capture_limit=128, allow_long_key=True)
        if cursor.skip_ws() != 0x3A:
            raise ValueError("expected colon in JSON object")
        cursor._pos += 1
        if key not in spec:
            cursor.skip_value()
        elif isinstance(spec[key], Mapping):
            if cursor.skip_ws() == 0x7B:
                result[key] = _scan_selected_object(
                    cursor, cast(Mapping[str, object], spec[key])
                )
            else:
                result[key] = cursor.read_small_value()
        else:
            result[key] = cursor.read_small_value()
        ch = cursor.skip_ws()
        if ch == 0x7D:
            cursor._pos += 1
            return result
        if ch != 0x2C:
            raise ValueError("expected comma or end of JSON object")
        cursor._pos += 1
        ch = cursor.skip_ws()


def _scan_turn_prefix_record(stream: BinaryIO, start: int, stop: int) -> dict[str, object]:
    """Decode only Turn/parent metadata from a large earlier record in O(chunk) memory."""
    cursor = _JsonSliceCursor(stream, start, stop)
    if cursor.skip_ws() != 0x7B:
        raise ValueError("native replay record must be a JSON object")
    record = _scan_selected_object(cursor, _PREFIX_RECORD_SPEC)
    if cursor.skip_ws() is not None:
        raise ValueError("trailing data in native replay record")
    return record
