from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any


class _MarkdownCell(str):
    pass


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def markdown_scalar(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").replace("|", "\\|").strip()


def markdown_code(value: Any) -> str:
    return _MarkdownCell(f"`{markdown_scalar(value)}`")


def markdown_table_row(cells: Iterable[Any]) -> str:
    rendered = (
        str(cell) if isinstance(cell, _MarkdownCell) else markdown_scalar(cell)
        for cell in cells
    )
    return "| " + " | ".join(rendered) + " |"


def markdown_table_separator(column_count: int) -> str:
    if column_count < 1:
        raise ValueError("markdown table must have at least one column")
    return markdown_table_row(["---"] * column_count)


def render_json_object_block(payload: dict[str, Any]) -> str:
    fields = ",\n".join(
        json.dumps(key, ensure_ascii=False) + ":"
        + json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for key, value in payload.items()
    )
    return "```json\n{\n" + fields + "\n}\n```"


def append_operator_action_markdown(
    lines: list[str],
    payload: dict[str, Any],
) -> None:
    action = as_dict(payload.get("operator_action"))
    if not action:
        return
    if payload.get("error_code"):
        lines.append(f"- error_code: `{markdown_scalar(payload.get('error_code'))}`")
    if payload.get("incident_channel"):
        lines.append(
            f"- incident_channel: `{markdown_scalar(payload.get('incident_channel'))}`"
        )
    lines.append(
        "- operator_action: "
        f"action={markdown_scalar(action.get('action'))} "
        f"holder_pid={markdown_scalar(action.get('holder_pid')) or 'unknown'} "
        f"retry_mode={markdown_scalar(action.get('retry_mode'))}"
    )
    for step in as_list(action.get("steps")):
        lines.append(f"  - {markdown_scalar(step)}")
