"""Bounded file-content capture for Explore evidence producers.

References cover the selected relative paths and bytes, not file modes, build
lineage, execution conditions, or independent verification of a measurement.
This host I/O helper does not normalize or compare measurement declarations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def candidate_snapshot(paths: list[Path], workspace: Path) -> tuple[str, list[dict[str, str]]]:
    files = []
    for path in paths:
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(workspace) or not resolved.is_file():
            raise ValueError("candidate files must be existing files in the workspace")
        with resolved.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        files.append({"path": resolved.relative_to(workspace).as_posix(), "sha256": digest})
    if not 1 <= len(files) <= 32 or len({row["path"] for row in files}) != len(files):
        raise ValueError("select one to 32 distinct candidate files")
    files.sort(key=lambda row: row["path"])
    return candidate_manifest_sha256(files), files


def candidate_manifest_sha256(files: list[dict[str, str]]) -> str:
    """Digest the canonical probe manifest, ordered by relative path."""
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()
