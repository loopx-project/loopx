"""Decode Chat journal rows for both complete and scoped history reads."""

import json
from typing import Any


def decode_chat_journal(lines: list[bytes], *, strict: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            if strict:
                raise ValueError("invalid Chat journal record") from exc
            continue
        if isinstance(item, dict):
            rows.append(item)
        elif strict:
            raise ValueError("invalid Chat journal record")
    return rows
