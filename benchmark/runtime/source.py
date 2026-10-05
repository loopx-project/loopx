"""Pinned product and research-runner archives; neither includes local state."""

from __future__ import annotations

import subprocess
from pathlib import Path


def checked_revision(source: Path, expected: str | None = None) -> str:
    head = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    if expected and head != expected:
        raise RuntimeError("Source does not match its expected revision")
    subprocess.run(
        ["git", "-C", str(source), "diff", "HEAD", "--exit-code", "--quiet"],
        check=True,
    )
    return head


def archive_source(source: Path, revision: str, output: Path, paths=()) -> None:
    with output.open("wb") as stream:
        subprocess.run(
            ["git", "-C", str(source), "archive", "--format=tar", revision, *paths],
            stdout=stream, check=True, timeout=120,
        )


def source_pins(source: Path, runner: Path, expected: str | None,
                expected_runner: str | None) -> tuple[str, str]:
    if source != runner and (not expected or not expected_runner):
        raise ValueError("Separate product and runner sources require both revision pins")
    head = checked_revision(source, expected)
    return head, head if source == runner else checked_revision(runner, expected_runner)
