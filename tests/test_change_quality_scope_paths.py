from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.change_quality.scope import build_change_quality_scope


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True
    )
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "base.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    return repo


def _scope(repo: Path) -> dict:
    return build_change_quality_scope(repo_path=repo, base_ref="HEAD")


@pytest.mark.parametrize("name", ["中文文件.txt", "café.txt", "ファイル.txt"])
def test_untracked_names_outside_ascii_keep_their_exact_spelling(
    tmp_path: Path, name: str
) -> None:
    """Git quotes non-ASCII names by default; the scope must still open them."""

    repo = _repository(tmp_path)
    (repo / name).write_text("content\n", encoding="utf-8")

    first = _scope(repo)
    assert first["changed_files"] == [name]
    assert first["sources"]["untracked"] == [name]

    second = _scope(repo)
    assert second["scope_fingerprint"] == first["scope_fingerprint"]


def test_untracked_names_keep_surrounding_spaces(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    name = "  padded file  .txt"
    (repo / name).write_text("content\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == [name]


@pytest.mark.skipif(os.name != "posix", reason="a newline in a path is POSIX-only")
def test_untracked_names_with_a_newline_stay_one_entry(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    name = "line\nbreak.txt"
    (repo / name).write_text("content\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == [name]
    assert scope["changed_file_count"] == 1


def test_changing_one_byte_of_an_untracked_file_changes_the_fingerprint(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    name = "中文文件.txt"
    (repo / name).write_text("content\n", encoding="utf-8")
    before = _scope(repo)["scope_fingerprint"]

    (repo / name).write_text("contenu\n", encoding="utf-8")
    after = _scope(repo)["scope_fingerprint"]

    assert after != before
