"""Provider-owned research data outside the native submission tree.

This composes the existing checkpoint component transport. It neither filters
submitted files nor changes the task's evaluator or feedback permissions.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from pathlib import PurePosixPath
import tarfile
from typing import Any


RESEARCH_DIRECTORY = "/tmp/loopx-benchmark-research"
RESEARCH_COMPONENT = "benchmark_research"
CORE_DUMP_EXCLUDE_PATTERN = "core-dump:elf-macho"
RESEARCH_EXCLUDE_PATTERNS = ("*.raw.gz", CORE_DUMP_EXCLUDE_PATTERN, "target/", ".cargo-target/")
_ELF_ET_CORE = 4
_MACHO_MH_CORE = 4


def is_core_dump_name(name: str) -> bool:
    """Return True only for POSIX core dump basenames (`core` or `core.*`)."""
    return name == "core" or name.startswith("core.")


def is_confirmed_core_dump_header(header: bytes) -> bool:
    """Return True only when leading file bytes prove an ELF ET_CORE or Mach-O MH_CORE dump."""
    if len(header) >= 18 and header[:4] == b"\x7fELF" and header[4] in (1, 2):
        if header[5] == 1:
            return int.from_bytes(header[16:18], "little") == _ELF_ET_CORE
        if header[5] == 2:
            return int.from_bytes(header[16:18], "big") == _ELF_ET_CORE
        return False
    if len(header) >= 16:
        magic = header[:4]
        if magic in (b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf"):
            return int.from_bytes(header[12:16], "big") == _MACHO_MH_CORE
        if magic in (b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe"):
            return int.from_bytes(header[12:16], "little") == _MACHO_MH_CORE
    return False


def research_component_exclusions(
    components: Sequence[Mapping[str, Any]],
) -> dict[str, list[str]]:
    """Return bounded source-exclusion patterns only when benchmark research is attached."""
    if any(entry.get("id") == RESEARCH_COMPONENT for entry in components if isinstance(entry, dict)):
        return {RESEARCH_COMPONENT: list(RESEARCH_EXCLUDE_PATTERNS)}
    return {}


def is_excluded_research_relpath(
    parts: tuple[str, ...],
    *,
    is_dir: bool,
    read_header: Callable[[], bytes] | None = None,
) -> bool:
    """Match a component-relative path tuple against RESEARCH_EXCLUDE_PATTERNS."""
    if not parts:
        return False
    for index, name in enumerate(parts):
        entry_is_dir = is_dir or index < len(parts) - 1
        for pattern in RESEARCH_EXCLUDE_PATTERNS:
            if pattern.endswith("/"):
                if entry_is_dir and name == pattern[:-1]:
                    return True
            elif pattern == CORE_DUMP_EXCLUDE_PATTERN:
                if (
                    not entry_is_dir
                    and index == len(parts) - 1
                    and is_core_dump_name(name)
                    and read_header is not None
                ):
                    try:
                        header = read_header()
                    except OSError:
                        header = b""
                    if is_confirmed_core_dump_header(header):
                        return True
            elif pattern.startswith("*."):
                if not entry_is_dir and name.endswith(pattern[1:]):
                    return True
            elif not entry_is_dir and name == pattern:
                return True
    return False


def is_excluded_tar_member(
    archive: tarfile.TarFile,
    member: tarfile.TarInfo,
    rel_parts: tuple[str, ...],
) -> bool:
    """Check tar member exclusion, confirming ELF/Mach-O core dump headers from archive bytes."""
    def _read_header() -> bytes:
        if not member.isfile():
            return b""
        try:
            stream = archive.extractfile(member)
        except (KeyError, OSError, tarfile.TarError):
            return b""
        if stream is None:
            return b""
        with stream:
            return stream.read(20)

    return is_excluded_research_relpath(rel_parts, is_dir=member.isdir(), read_header=_read_header)


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
