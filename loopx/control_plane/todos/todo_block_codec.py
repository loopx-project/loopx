"""One legacy Markdown row decoder for reads and edits; no identity or authority writes."""
from typing import Any

from .contract import TODO_TASK_PATTERN, parse_todo_metadata_line, todo_done_for_status, todo_status_from_marker
from .todo_summary import normalize_todo_text


def decode_todo_blocks(
    lines: list[str], start: int, end: int, *, visible: frozenset[int],
    text_limit: int | None = 500,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for index in range(start + 1, end):
        if index not in visible:
            if current is not None:
                current["end"] = index
                current = None
            continue
        match = TODO_TASK_PATTERN.match(lines[index])
        if match:
            if current is not None:
                current["end"] = index
            marker, text = match.groups()
            status = todo_status_from_marker(marker)
            current = {"start": index, "end": end, "index": len(blocks) + 1,
                       "done": todo_done_for_status(status), "status": status,
                       "text": normalize_todo_text(text, limit=text_limit)}
            blocks.append(current)
        elif current is not None and lines[index].startswith((" ", "\t")):
            metadata = parse_todo_metadata_line(lines[index])
            if metadata:
                current.update(metadata)
            elif continuation := lines[index].strip():
                current["text"] = normalize_todo_text(
                    f"{current['text']} {continuation}", limit=text_limit,
                )
    return blocks
