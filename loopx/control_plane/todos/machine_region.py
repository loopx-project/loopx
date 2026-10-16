"""Versioned ownership of generated Todo regions, not a general Markdown parser.

Legacy import accepts the actual LoopX-produced H2/list/metadata grammar.
New projections have paired markers, shared by the writer and readers.
Text outside that grammar is never silently adopted as machine-owned content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .contract import TODO_TASK_PATTERN, parse_todo_metadata_line
from ..goals.active_state_metadata import TODO_ARCHIVE_HEADER_MARKERS, todo_role_for_heading


TODO_REGION_PREFIX = "<!-- loopx:todo-region-v0 "
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_ROLE_HEADINGS = {
    "user todo": "user",
    "user todo / owner review reading queue": "user",
    "owner review reading queue": "user",
    "owner reading queue": "user",
    "agent todo": "agent",
    "codex todo": "agent",
    "project agent todo": "agent",
    "completed work archive": "archive",
}


@dataclass(frozen=True)
class TodoRegion:
    role: str
    start: int
    end: int
    body_end: int
    heading: str
    marked: bool


def todo_region_marker(role: str, edge: str) -> str:
    if role not in {"user", "agent", "archive"} or edge not in {"begin", "end"}:
        raise ValueError("invalid Todo region marker")
    return f"{TODO_REGION_PREFIX}role={role} {edge} -->"


def visible_markdown_lines(lines: list[str]) -> frozenset[int]:
    """Line ownership shared by Todo readers, editors and generated projections."""
    visible: set[int] = set()
    fence: str | None = None
    in_comment = False
    index = 0
    if lines and lines[0].strip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
        if end is None:
            raise ValueError("unterminated Goal frontmatter")
        index = end + 1
    while index < len(lines):
        line = lines[index].rstrip("\r\n")
        if fence is not None:
            if re.fullmatch(r" {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}[ \t]*", line):
                fence = None
        elif in_comment:
            in_comment = "-->" not in line
        elif opening := _FENCE.fullmatch(line):
            fence = opening.group(1)
        elif line.lstrip().startswith("<!--") and "-->" not in line:
            in_comment = True
        else:
            visible.add(index)
        index += 1
    return frozenset(visible)


def find_todo_regions(lines: list[str], *, visible: frozenset[int] | None = None) -> list[TodoRegion]:
    """Find replaceable generated regions; never adopt surrounding narrative."""
    visible = visible_markdown_lines(lines) if visible is None else visible
    regions: list[TodoRegion] = []
    index = 0
    while index < len(lines):
        if index not in visible:
            index += 1
            continue
        line = lines[index].rstrip("\r\n")
        if TODO_REGION_PREFIX in line:
            raise ValueError("orphan or malformed Todo region marker")
        heading = line[3:].strip() if line.startswith("## ") else ""
        role = _ROLE_HEADINGS.get(heading.lower())
        if role is None:
            index += 1
            continue
        start = index
        index += 1
        marked = index < len(lines) and lines[index].rstrip("\r\n") == todo_region_marker(role, "begin")
        if marked:
            index += 1
        legacy_task_open = False
        while index < len(lines):
            content = lines[index].rstrip("\r\n")
            if content == todo_region_marker(role, "end") and marked:
                break
            if TODO_REGION_PREFIX in content:
                raise ValueError("nested, mismatched, or malformed Todo region marker")
            task = TODO_TASK_PATTERN.match(content) is not None
            legacy_continuation = (
                not marked and legacy_task_open and content.startswith((" ", "\t"))
            )
            generated = index in visible and (
                not content.strip()
                or task
                or parse_todo_metadata_line(content) is not None
                or content.startswith("<!-- loopx:todo-section-projection-v0 ")
                or legacy_continuation
            )
            if not generated:
                if marked:
                    raise ValueError("non-generated content inside marked Todo region")
                break
            # Adopt the old writer's folded task as one block. A blank line
            # closes that block so following indented narrative stays opaque;
            # marked regions retain their stricter generated-only grammar.
            if task:
                legacy_task_open = True
            elif not content.strip():
                legacy_task_open = False
            index += 1
        if marked and index == len(lines):
            raise ValueError("unterminated Todo region")
        body_end = index
        if marked:
            index += 1
        regions.append(TodoRegion(role, start, index, body_end, heading, marked))
    return regions


def find_todo_source_regions(lines: list[str], *, visible: frozenset[int] | None = None) -> list[TodoRegion]:
    """Read/edit marked regions strictly; retain unmarked legacy heading aliases and continuations.

    Existing heading-substring aliases are a legacy input compatibility contract,
    not a classifier for generated ownership. They can retire with that importer.
    """
    visible = visible_markdown_lines(lines) if visible is None else visible
    generated = find_todo_regions(lines, visible=visible)
    if any(region.marked for region in generated):
        return generated
    headings = [index for index in sorted(visible) if lines[index].startswith("## ")]
    regions: list[TodoRegion] = []
    for position, start in enumerate(headings):
        heading = lines[start][3:].strip()
        role = "archive" if any(marker in heading.lower() for marker in TODO_ARCHIVE_HEADER_MARKERS) else todo_role_for_heading(heading)
        if role is None:
            continue
        end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        regions.append(TodoRegion(role, start, end, end, heading, False))
    return regions
