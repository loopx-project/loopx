"""Book-authored, zero-permission teaching provider; not a LoopX built-in."""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Sequence
from typing import Any

EXTENSION_ID = "loopx-text-stats"
REQUEST_SCHEMA = "loopx_text_stats_request_v0"
RESPONSE_SCHEMA = "loopx_text_stats_response_v0"
MAX_INPUT_BYTES = 65536
MAX_TEXT_CHARACTERS = 32000

class InvalidRequest(ValueError):
    """Carries a bounded public error code, never the rejected payload."""

def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidRequest("duplicate_field")
        result[key] = value
    return result

def analyze_text(text: str) -> dict[str, int]:
    """Characters are Python Unicode code points; words are whitespace chunks."""
    return {
        "characters": len(text),
        "non_whitespace_characters": sum(not c.isspace() for c in text),
        "words": len(re.findall(r"\S+", text)),
        "lines": len(text.splitlines()) or 1,
    }

def decode_request(data: bytes) -> str:
    if len(data) > MAX_INPUT_BYTES:
        raise InvalidRequest("input_too_large")
    try:
        request = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object)
    except InvalidRequest:
        # Preserve the specific duplicate-field error from object_pairs_hook.
        raise
    except (ValueError, RecursionError) as exc:
        # ValueError includes UTF-8/JSON errors and Python's integer digit limit.
        # Do not relax the interpreter limit or expose the parser's message.
        raise InvalidRequest("invalid_json") from exc
    if not isinstance(request, dict):
        raise InvalidRequest("object_required")
    if set(request) != {"schema_version", "text"}:
        raise InvalidRequest("invalid_fields")
    if request["schema_version"] != REQUEST_SCHEMA:
        raise InvalidRequest("schema_mismatch")
    text = request["text"]
    if not isinstance(text, str) or not text.strip():
        raise InvalidRequest("nonempty_text_required")
    if len(text) > MAX_TEXT_CHARACTERS:
        raise InvalidRequest("text_too_long")
    return text

def response(data: bytes) -> tuple[int, dict[str, Any]]:
    base: dict[str, Any] = {"schema_version": RESPONSE_SCHEMA, "extension_id": EXTENSION_ID}
    try:
        text = decode_request(data)
    except InvalidRequest as exc:
        return 1, {**base, "ok": False, "error": str(exc)}
    return 0, {**base, "ok": True, "request_schema_version": REQUEST_SCHEMA,
               "result": analyze_text(text)}

def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=EXTENSION_ID)
    parser.add_argument("--doctor", action="store_true")
    args = parser.parse_args(argv)
    if args.doctor:
        return 0
    try:
        code, payload = response(sys.stdin.buffer.read(MAX_INPUT_BYTES + 1))
    except OSError:
        code, payload = 1, {"schema_version": RESPONSE_SCHEMA, "extension_id": EXTENSION_ID,
                            "ok": False, "error": "input_unavailable"}
    json.dump(payload, sys.stdout, ensure_ascii=True, sort_keys=True)
    sys.stdout.write("\n")
    return code

if __name__ == "__main__":
    raise SystemExit(main())
