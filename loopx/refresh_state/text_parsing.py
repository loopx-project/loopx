"""Markdown frontmatter, section, and action-line helpers for state refresh."""

from __future__ import annotations

import re

from ..feedback import validate_local_control_text

BULLET_PREFIX_RE = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)")
CHECKBOX_PREFIX_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s+")


def parse_frontmatter(text: str) -> dict[str, str]:
    """Parse key/value pairs from leading Markdown YAML frontmatter."""
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    values: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"')
    return values


def extract_section_lines(text: str, heading: str, limit: int = 8) -> list[str]:
    """Collect up to ``limit`` non-empty lines under a ``## <heading>`` section."""
    in_section = False
    collected: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if in_section:
                break
            in_section = line[3:].strip() == heading
            continue
        if in_section and line.strip():
            collected.append(line.strip())
            if len(collected) >= limit:
                break
    return collected


def replace_updated_at(text: str, updated_at: str) -> str:
    """Update or insert ``updated_at`` in leading Markdown frontmatter."""
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


def normalize_next_action_text(value: str) -> str:
    """Collapse whitespace and enforce local-control safety on ``next_action``."""
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError("next_action must not be empty")
    validate_local_control_text("active_state_next_action", text)
    return text


def clean_action_line(line: str) -> str:
    """Strip leading Markdown list bullets and task-checkbox markers."""
    text = BULLET_PREFIX_RE.sub("", line.strip()).strip()
    return CHECKBOX_PREFIX_RE.sub("", text).strip()


def is_bullet_line(line: str) -> bool:
    """Return True when ``line`` starts with a bullet or numbered list prefix."""
    return bool(BULLET_PREFIX_RE.match(line.strip()))


def first_action_item(lines: list[str], start: int) -> str:
    """Join one action item and its non-comment continuation lines."""
    first_line = lines[start]
    parts = [clean_action_line(first_line)]
    if is_bullet_line(first_line):
        for line in lines[start + 1 :]:
            if is_bullet_line(line):
                break
            if line.strip().startswith("<!--"):
                continue
            cleaned = clean_action_line(line)
            if cleaned:
                parts.append(cleaned)
    return " ".join(part for part in parts if part).strip()


def section_list_items(lines: list[str]) -> list[str]:
    """Normalize raw section lines into flat bullet text items."""
    items: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if is_bullet_line(line):
            item = first_action_item(lines, index)
            if item:
                items.append(item)
            index += 1
            while index < len(lines) and not is_bullet_line(lines[index]):
                index += 1
            continue
        cleaned = clean_action_line(line)
        if cleaned:
            items.append(cleaned)
        index += 1
    return items
