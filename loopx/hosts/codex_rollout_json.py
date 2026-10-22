"""Bounded-memory JSONL envelope reader for the local Codex observer.

Only type/timestamp/payload.type survive parsing. Large message strings are
validated in chunks, never accumulated or written to the observation cache.
This is provider IO, not another owner of host execution state.
"""

from __future__ import annotations

import codecs
import json
import re
from typing import Any

SMALL_RECORD_BYTES = 128 * 1024
_SPECIAL = re.compile(r'["\\\x00-\x1f]')
_HEX = frozenset("0123456789abcdefABCDEF")


class _Envelope:
    def __init__(self) -> None:
        self.decoder = codecs.getincrementaldecoder("utf-8")()
        self.stack: list[dict[str, Any]] = []
        self.root = "value"
        self.root_object = False
        self.string = False
        self.capture = False
        self.token = ""
        self.escape = False
        self.unicode_left = 0
        self.bare = False
        self.invalid = False
        self.facts: dict[str, Any] = {}

    def _path(self) -> tuple[str, ...] | None:
        if not self.stack:
            return ()
        frame = self.stack[-1]
        path = frame["path"]
        return (
            (*path, frame["key"])
            if frame["kind"] == "{" and path is not None and len(path) < 2
            else None
        )

    def _value_expected(self) -> bool:
        return (
            self.root == "value"
            if not self.stack
            else self.stack[-1]["phase"] in ("value", "value_or_end")
        )

    def _note(self, value: Any) -> None:
        path = self._path()
        if path == ("timestamp",):
            self.facts["timestamp"] = value if isinstance(value, str) else None
        elif path == ("type",):
            self.facts["type"] = value if isinstance(value, str) else None
        elif path == ("payload",):
            self.facts["payload_object"] = isinstance(value, dict)
            self.facts["payload_type"] = None
            self.facts["id"] = self.facts["session_id"] = None
        elif path == ("payload", "type"):
            self.facts["payload_type"] = value if isinstance(value, str) else None
        elif path in (("payload", "id"), ("payload", "session_id")):
            self.facts[path[-1]] = value if isinstance(value, str) else None

    def _complete(self, value: Any, *, container: bool = False) -> None:
        if not container:
            self._note(value)
        if self.stack:
            self.stack[-1]["phase"] = "comma"
        else:
            self.root = "complete"

    def _string_done(self) -> None:
        frame = self.stack[-1] if self.stack else None
        key = (
            frame is not None
            and frame["kind"] == "{"
            and frame["phase"] in ("key", "key_or_end")
        )
        value = json.loads('"' + self.token + '"') if self.capture else None
        if key:
            assert frame is not None
            frame.update(key=value, phase="colon")
        else:
            self._complete(value)
        self.token = ""
        self.string = False

    def _bare_done(self) -> None:
        value = json.loads(self.token)
        if isinstance(value, (dict, list, str)) or self.token in (
            "NaN",
            "Infinity",
            "-Infinity",
        ):
            raise ValueError("invalid primitive")
        self._complete(value)
        self.token = ""
        self.bare = False

    def _text(self, text: str) -> None:
        i = 0
        while i < len(text):
            char = text[i]
            if self.string:
                if self.unicode_left:
                    if char not in _HEX:
                        raise ValueError("invalid unicode escape")
                    self.unicode_left -= 1
                elif self.escape:
                    if char not in '"\\/bfnrtu':
                        raise ValueError("invalid escape")
                    self.unicode_left = 4 if char == "u" else 0
                    self.escape = False
                else:
                    match = _SPECIAL.search(text, i)
                    end = match.start() if match else len(text)
                    if self.capture:
                        self.token += text[i:end]
                        if len(self.token) > 512:
                            raise ValueError("oversized metadata")
                    i = end
                    if i == len(text):
                        break
                    char = text[i]
                    if char == '"':
                        self._string_done()
                        i += 1
                        continue
                    if char != "\\":
                        raise ValueError("control character")
                    self.escape = True
                if self.capture:
                    self.token += char
                if len(self.token) > 512:
                    # Oversized keys/metadata are not a recognized host shape.
                    raise ValueError("oversized metadata")
                i += 1
                continue
            if self.bare:
                if char not in " \t\r\n,]}":
                    self.token += char
                    if len(self.token) > 128:
                        raise ValueError("oversized primitive")
                    i += 1
                    continue
                self._bare_done()
                continue
            if char in " \t\r\n":
                i += 1
                continue
            frame = self.stack[-1] if self.stack else None
            if char == '"':
                key = (
                    frame is not None
                    and frame["kind"] == "{"
                    and frame["phase"] in ("key", "key_or_end")
                )
                if not key and not self._value_expected():
                    raise ValueError("unexpected string")
                self.capture = bool(
                    key and frame is not None and frame["path"] in ((), ("payload",))
                ) or self._path() in (
                    ("timestamp",),
                    ("type",),
                    ("payload", "type"),
                    ("payload", "id"),
                    ("payload", "session_id"),
                )
                self.string = True
            elif char in "{[":
                if not self._value_expected() or len(self.stack) >= 128:
                    raise ValueError("unexpected container")
                path = self._path()
                self._note({} if char == "{" else [])
                if not self.stack:
                    self.root_object = char == "{"
                self.stack.append(
                    {
                        "kind": char,
                        "path": path,
                        "key": None,
                        "phase": "key_or_end" if char == "{" else "value_or_end",
                    }
                )
            elif char in "}]":
                expected = "{" if char == "}" else "["
                if (
                    frame is None
                    or frame["kind"] != expected
                    or frame["phase"] not in ("comma", "key_or_end", "value_or_end")
                ):
                    raise ValueError("unexpected container end")
                self.stack.pop()
                self._complete(None, container=True)
            elif (
                char == ":"
                and frame is not None
                and frame["kind"] == "{"
                and frame["phase"] == "colon"
            ):
                frame["phase"] = "value"
            elif char == "," and frame is not None and frame["phase"] == "comma":
                frame["phase"] = "key" if frame["kind"] == "{" else "value"
            elif char in "-0123456789tfn" and self._value_expected():
                self.bare = True
                self.token = char
            else:
                raise ValueError("unexpected JSON token")
            i += 1

    def feed(self, data: bytes) -> None:
        if self.invalid:
            return
        try:
            self._text(self.decoder.decode(data))
        except (UnicodeError, ValueError):
            self.invalid = True

    def finish(self, *, header: bool = False) -> dict[str, Any] | None:
        try:
            self._text(self.decoder.decode(b"", final=True))
            if self.bare:
                self._bare_done()
        except (UnicodeError, ValueError):
            self.invalid = True
        if (
            self.invalid
            or self.string
            or self.stack
            or self.root != "complete"
            or not self.root_object
        ):
            return None
        kind, timestamp = self.facts.get("type"), self.facts.get("timestamp")
        if not isinstance(kind, str) or (not header and not isinstance(timestamp, str)):
            return None
        payload = (
            {"type": self.facts.get("payload_type")}
            if self.facts.get("payload_object")
            else None
        )
        if header and payload is not None:
            payload.update(
                id=self.facts.get("id"), session_id=self.facts.get("session_id")
            )
        return {"type": kind, "timestamp": timestamp, "payload": payload}


class MetadataLine:
    """Fast small records; streaming validation for a single arbitrarily long line."""

    def __init__(self) -> None:
        self.small = bytearray()
        self.stream: _Envelope | None = None
        self.nonblank = False

    def feed(self, data: bytes) -> None:
        self.nonblank = self.nonblank or bool(data.strip())
        if self.stream is None and len(self.small) + len(data) <= SMALL_RECORD_BYTES:
            self.small.extend(data)
            return
        if self.stream is None:
            self.stream = _Envelope()
            self.stream.feed(bytes(self.small))
            self.small.clear()
        self.stream.feed(data)

    def finish(self, *, header: bool = False) -> dict[str, Any] | None:
        if self.stream is not None:
            return self.stream.finish(header=header)
        try:
            record = json.loads(self.small)
        except (UnicodeError, ValueError, RecursionError):
            return None
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("type"), str)
            or (not header and not isinstance(record.get("timestamp"), str))
        ):
            return None
        payload = record.get("payload")
        if len(record["type"]) > 512 or (not header and len(record["timestamp"]) > 512):
            return None
        kind = payload.get("type") if isinstance(payload, dict) else None
        kind = kind if isinstance(kind, str) and len(kind) <= 512 else None
        result = {
            "type": record["type"],
            "timestamp": record.get("timestamp"),
            "payload": {"type": kind} if isinstance(payload, dict) else None,
        }
        if header and isinstance(payload, dict):
            result["payload"].update(
                id=payload.get("id"), session_id=payload.get("session_id")
            )
        return result
