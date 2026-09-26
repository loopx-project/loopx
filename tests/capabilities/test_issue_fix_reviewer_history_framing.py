from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix.reviewer_recommendation import _collect_history

AUTHOR_NAME_WITH_SEPARATOR = "Zo{separator}e"


def _git(
    repo: Path,
    *args: str,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    return result.stdout.strip()


def _repository_with_separator_commit(tmp_path: Path, separator: str) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")

    path = "shared.txt"
    (repo / path).write_text("base\n", encoding="utf-8")
    _git(repo, "add", path)
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME=AUTHOR_NAME_WITH_SEPARATOR.format(separator=separator),
        GIT_AUTHOR_EMAIL="z@example.invalid",
        GIT_COMMITTER_NAME="LoopX Test",
        GIT_COMMITTER_EMAIL="loopx@example.invalid",
    )
    _git(repo, "commit", "-m", "author name containing a separator", env=env)
    return repo, path


@pytest.mark.parametrize("separator", ["\x85", "\u2028"])
def test_collect_history_frames_records_on_lf_not_splitlines(
    tmp_path: Path,
    separator: str,
) -> None:
    """One `git log --format` record must stay one row.

    A name containing U+0085 or U+2028 is emitted verbatim, and
    `str.splitlines()` treats both as line breaks. Framing on them tore the
    record in two; the fragment before the field separator was then dropped by
    the malformed-line guard and the contributor was published as the trailing
    fragment.
    """
    repo, path = _repository_with_separator_commit(tmp_path, separator)

    rows = _collect_history(repo, path, revision="HEAD", history_limit=10)

    assert len(rows) == 1
    # `public_safe_compact_text` folds the separator to a space. The regression
    # assertion is the whole name surviving, not the folded character.
    assert rows == [("Zo e", "z@example.invalid")]


def test_collect_history_keeps_ordinary_ascii_names_unchanged(tmp_path: Path) -> None:
    repo, path = _repository_with_separator_commit(tmp_path, "")

    rows = _collect_history(repo, path, revision="HEAD", history_limit=10)

    assert rows == [("Zoe", "z@example.invalid")]
