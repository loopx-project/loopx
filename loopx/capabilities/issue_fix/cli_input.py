from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


_MAX_INLINE_JSON_CHARS = 1_048_576


def load_json_object(input_text: str) -> dict[str, Any]:
    stripped = input_text.lstrip()
    if input_text == "-":
        source = "stdin"
        raw = sys.stdin.read()
    elif stripped.startswith(("{", "[")):
        source = "inline"
        raw = input_text
    else:
        source = "file"
        try:
            raw = Path(input_text).expanduser().read_text(encoding="utf-8")
        except (OSError, RuntimeError):
            raise ValueError("could not read JSON input file") from None
    if source == "inline" and len(raw) > _MAX_INLINE_JSON_CHARS:
        raise ValueError("inline JSON input exceeds the 1 MiB limit")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("JSON input is invalid") from None
    if not isinstance(payload, dict):
        raise ValueError("JSON input must contain an object")
    return payload


def load_jsonl_row(
    path: Path,
    *,
    repo: str,
    ref_field: str,
    ref_value: str,
) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"issue-fix domain-state source is missing: {path.name}")
    match: dict[str, Any] | None = None
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} is not valid JSON") from exc
        observation = row.get("observation") if isinstance(row, dict) else None
        if not isinstance(observation, dict):
            continue
        if (
            str(observation.get("repo") or "").strip() == repo
            and str(observation.get(ref_field) or "").strip() == ref_value
        ):
            match = row
    if match is None:
        raise ValueError(
            f"{path.name} has no row for repo={repo} {ref_field}={ref_value}"
        )
    return match


def load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"issue-fix domain-state source is missing: {path.name}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").split("\n"), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name}:{line_number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{path.name}:{line_number} must contain an object")
        rows.append(row)
    return rows
