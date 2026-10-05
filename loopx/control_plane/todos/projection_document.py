"""Parse replaceable Todo regions once, preserving every byte of surrounding prose."""
from collections.abc import Mapping
from dataclasses import dataclass
import json

from .machine_region import TodoRegion, find_todo_regions, visible_markdown_lines


def recovered_todo_projection_skeleton(goal_id: str) -> str:
    """The display recovery codec; it does not restore missing Goal intent."""
    return (
        f"---\ngoal_id: {json.dumps(goal_id, ensure_ascii=False)}\n---\n\n"
        "# Recovered Todo projection\n\n"
        "> Regenerated from canonical Todo authority. Non-Todo sections "
        "are not in this provider snapshot and were not recovered. "
        "This is a Todo projection, not a complete Goal-state restore.\n\n"
        "## Agent Todo\n"
    )


@dataclass(frozen=True)
class TodoProjectionDocument:
    markdown: str
    lines: list[str]
    visible: frozenset[int]
    regions: tuple[TodoRegion, ...]
    spans: Mapping[str, tuple[int, int]]
    narrative: str

    @classmethod
    def parse(cls, markdown: str) -> "TodoProjectionDocument":
        lines = markdown.splitlines(keepends=True)
        visible = visible_markdown_lines(lines)
        regions = tuple(find_todo_regions(lines, visible=visible))
        offsets = [0]
        for line in lines:
            offsets.append(offsets[-1] + len(line))
        spans: dict[str, tuple[int, int]] = {}
        narrative: list[str] = []
        cursor = 0
        for region in regions:
            if region.role in spans:
                raise ValueError(f"active Markdown contains multiple {region.role} Todo sections")
            start, end = offsets[region.start], offsets[region.end]
            spans[region.role] = start, end
            narrative.append(markdown[cursor:start])
            cursor = end
        narrative.append(markdown[cursor:])
        return cls(markdown, lines, visible, regions, spans, "".join(narrative))

    def replace(self, sections: Mapping[str, str]) -> str:
        if not self.spans:
            raise ValueError("active Markdown omits required Todo sections: no projection anchor")
        replacements = {role: sections[role] for role in self.spans}
        roles = list(replacements)
        prefix = sections["user"] if "user" not in replacements else ""
        if "agent" not in replacements:
            if "user" in replacements:
                replacements["user"] += sections["agent"]
            else:
                prefix += sections["agent"]
        if "archive" in sections and "archive" not in replacements:
            replacements[roles[-1]] += sections["archive"]
        replacements[roles[0]] = prefix + replacements[roles[0]]
        result = self.markdown
        for role, (start, end) in reversed(list(self.spans.items())):
            result = result[:start] + replacements[role] + result[end:]
        return result
