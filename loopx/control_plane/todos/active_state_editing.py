from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contract import (
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    build_todo_id,
    normalize_todo_status,
    parse_todo_metadata_line,
    todo_done_for_status,
    todo_marker_for_status,
)
from .text import normalize_new_todo
from .todo_summary import normalize_todo_text
from .machine_region import find_todo_source_regions, visible_markdown_lines
from .todo_block_codec import decode_todo_blocks


TODO_SECTION_HEADINGS = {
    "user": "User Todo / Owner Review Reading Queue",
    "agent": "Agent Todo",
}
COMPLETED_WORK_ARCHIVE_HEADING = "Completed Work Archive"


def section_bounds(lines: list[str], role: str) -> tuple[int, int, str] | None:
    region = next((region for region in find_todo_source_regions(lines) if region.role == role), None)
    return (region.start, region.body_end, region.heading) if region else None


def find_todo_block(
    lines: list[str],
    *,
    todo_id: str,
    role: str | None = None,
) -> tuple[str, str, int, int, dict[str, Any]] | None:
    roles = [role] if role else list(TODO_SECTION_HEADINGS)
    for candidate_role in roles:
        if candidate_role not in TODO_SECTION_HEADINGS:
            continue
        bounds = section_bounds(lines, candidate_role)
        if not bounds:
            continue
        start, end, section = bounds
        for block in todo_blocks(
            lines,
            start,
            end,
            role=candidate_role,
            source_section=section,
        ):
            if block.get("todo_id") == todo_id:
                return candidate_role, section, start, end, block
    return None


def todo_metadata_would_change(
    lines: list[str],
    block: dict[str, Any],
    metadata_line: str | None,
) -> bool:
    if not metadata_line:
        return False
    start = int(block["start"])
    end = int(block["end"])
    for index in range(start + 1, end):
        if parse_todo_metadata_line(lines[index]) is not None:
            return lines[index] != metadata_line
    return True


def set_todo_marker(lines: list[str], block: dict[str, Any], status: str) -> bool:
    marker = todo_marker_for_status(status)
    index = int(block["start"])
    updated = re.sub(
        r"^(\s*[-*]\s+\[)[ xX-](\]\s+)",
        rf"\1{marker}\2",
        lines[index],
        count=1,
    )
    if updated == lines[index]:
        return False
    lines[index] = updated
    return True


def set_todo_text(
    lines: list[str],
    block: dict[str, Any],
    text: str,
    *,
    status: str,
) -> bool:
    normalized = normalize_new_todo(text)
    if normalize_todo_text(str(block.get("text") or "")) == normalized:
        return False

    start = int(block["start"])
    end = int(block["end"])
    marker = todo_marker_for_status(status)
    replacement = f"- [{marker}] {normalized}"
    continuation_indexes = [
        index
        for index in range(start + 1, end)
        if lines[index].startswith((" ", "\t"))
        and parse_todo_metadata_line(lines[index]) is None
    ]
    lines[start] = replacement
    for index in reversed(continuation_indexes):
        del lines[index]
    block["text"] = normalized
    block["end"] = end - len(continuation_indexes)
    return True


def heading_index(lines: list[str], heading: str) -> int | None:
    needle = f"## {heading}"
    for index in sorted(visible_markdown_lines(lines)):
        if lines[index].strip() == needle:
            return index
    return None


def insert_into_existing_section(lines: list[str], start: int, end: int, todo_line: str) -> None:
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    new_lines = todo_line.splitlines()
    if insert_at == start + 1:
        new_lines.insert(0, "")
    if insert_at == end and end < len(lines) and lines[end].startswith("## "):
        new_lines.append("")
    lines[insert_at:insert_at] = new_lines


def insertion_anchor(lines: list[str], role: str) -> int:
    if role == "user":
        agent_bounds = section_bounds(lines, "agent")
        if agent_bounds:
            return agent_bounds[0]
    next_action = heading_index(lines, "Next Action")
    if next_action is not None:
        return next_action
    return len(lines)


def insert_new_section(lines: list[str], role: str, todo_line: str) -> None:
    anchor = insertion_anchor(lines, role)
    heading = TODO_SECTION_HEADINGS[role]
    section = [f"## {heading}", "", *todo_line.splitlines(), ""]
    if anchor > 0 and lines[anchor - 1].strip():
        section.insert(0, "")
    lines[anchor:anchor] = section


def archive_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    region = next((region for region in find_todo_source_regions(lines) if region.role == "archive"), None)
    return (region.start, region.body_end) if region else None


def ensure_archive_section(lines: list[str]) -> tuple[int, int]:
    bounds = archive_section_bounds(lines)
    if bounds:
        return bounds
    if lines and lines[-1].strip():
        lines.append("")
    start = len(lines)
    lines.extend([f"## {COMPLETED_WORK_ARCHIVE_HEADING}", ""])
    return start, len(lines)


def ensure_block_identity(
    block: dict[str, Any],
    *,
    role: str | None,
    source_section: str | None,
) -> dict[str, Any]:
    if block.get("status"):
        status = normalize_todo_status(block.get("status")) or TODO_STATUS_OPEN
    else:
        status = TODO_STATUS_DONE if block.get("done") else TODO_STATUS_OPEN
    block["status"] = status
    block["done"] = todo_done_for_status(status)
    if not block.get("todo_id"):
        block["todo_id"] = build_todo_id(
            role=role,
            source_section=source_section,
            index=block.get("index"),
            text=block.get("text"),
        )
    return block


def todo_blocks(
    lines: list[str],
    start: int,
    end: int,
    *,
    role: str | None = None,
    source_section: str | None = None,
) -> list[dict[str, Any]]:
    blocks = decode_todo_blocks(lines, start, end, visible=visible_markdown_lines(lines))
    for block in blocks:
        ensure_block_identity(block, role=role, source_section=source_section)
    return blocks


def insert_archive_blocks(lines: list[str], blocks: list[list[str]]) -> None:
    if not blocks:
        return
    bounds = ensure_archive_section(lines)
    insert_at = bounds[1]
    while insert_at > bounds[0] + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    new_lines: list[str] = []
    if insert_at == bounds[0] + 1:
        new_lines.append("")
    for block in blocks:
        new_lines.extend(block)
    if insert_at < len(lines) and lines[insert_at].startswith("## "):
        new_lines.append("")
    lines[insert_at:insert_at] = new_lines


def replace_updated_at(text: str, updated_at: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    frontmatter = parts[1]
    body = parts[2]
    if re.search(r"(?m)^updated_at:\s*.+$", frontmatter):
        frontmatter = re.sub(
            r"(?m)^updated_at:\s*.+$",
            f"updated_at: {updated_at}",
            frontmatter,
            count=1,
        )
    else:
        frontmatter = frontmatter.rstrip("\n") + f"\nupdated_at: {updated_at}\n"
    return "---" + frontmatter + "---" + body
