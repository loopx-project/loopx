"""Provider-owned research data outside the native submission tree.

This composes the existing checkpoint component transport. It neither filters
submitted files nor changes the task's evaluator or feedback permissions.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath


RESEARCH_DIRECTORY = "/tmp/loopx-benchmark-research"
RESEARCH_COMPONENT = "benchmark_research"


def validate_research_scope(workspace: str, submit_paths: list[str]) -> None:
    """Refuse an overlapping task instead of silently changing submission scope."""
    root = PurePosixPath(workspace)
    research = PurePosixPath(RESEARCH_DIRECTORY)
    roots = [root, *(PurePosixPath(path) if PurePosixPath(path).is_absolute()
                     else root / path for path in submit_paths)]
    for path in roots:
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("Research storage requires normalized absolute task and submission paths")
        if research.is_relative_to(path) or path.is_relative_to(research):
            raise ValueError("Research storage overlaps the task or its submitted paths")


def with_research_capture(request: dict) -> dict:
    """Add this runner's data to a copy of its explicit capture declaration."""
    result = deepcopy(request)
    expected = {"id": RESEARCH_COMPONENT, "source_path": RESEARCH_DIRECTORY}
    present = False
    for component in result["components"]:
        if component.get("id") == RESEARCH_COMPONENT:
            if component != expected or present:
                raise ValueError("Reserved research component conflicts with the checkpoint declaration")
            present = True
        else:
            source = PurePosixPath(component["source_path"])
            research = PurePosixPath(RESEARCH_DIRECTORY)
            if source.is_relative_to(research) or research.is_relative_to(source):
                raise ValueError("Checkpoint component overlaps the reserved research directory")
    if not present:
        result["components"].append(expected)
    return result


def selected_research_components(metadata: dict, selected, *, managed: bool):
    """Only the runner's exact sealed component joins explicit receiver choices.

    Older checkpoints do not have this component. Their original workspace and
    explicit research selections remain intact; no historical files are moved.
    """
    if not managed:
        return selected
    if (not isinstance(selected, (tuple, list)) or
            any(not isinstance(key, str) for key in selected) or len(set(selected)) != len(selected)):
        raise ValueError("Research component selection must contain distinct captured IDs")
    matches = [entry for entry in metadata["worker_file_snapshot"]["components"]
               if entry["id"] == RESEARCH_COMPONENT]
    if not matches:
        return selected
    if len(matches) != 1 or matches[0] != {"id": RESEARCH_COMPONENT, "source_path": RESEARCH_DIRECTORY}:
        raise ValueError("Captured runner research component has a conflicting identity")
    return tuple(selected) if RESEARCH_COMPONENT in selected else (*selected, RESEARCH_COMPONENT)
