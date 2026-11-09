from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix.acceptance_loop import (
    _changed_files,
    build_issue_fix_caller_repo_branch_packet,
)


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
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    subprocess.run(["git", "-C", str(repo), "checkout", "-qb", "issue"], check=True)
    return repo


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_untracked_names_keep_the_bytes_git_reported(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    names = [
        "中文文件.txt",
        "  padded.txt  ",
        "line\nbreak.txt",
        "back\\slash.txt",
        "has space.txt",
    ]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is False
    assert sorted(files) == sorted(names)


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_committed_staged_and_untracked_sources_agree(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    committed = "更名内容.txt"
    staged = "  缩进  .txt"
    untracked = "line\nbreak.txt"

    (repo / committed).write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", committed], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add committed"], check=True)
    (repo / staged).write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", staged], check=True)
    (repo / untracked).write_text("untracked\n", encoding="utf-8")

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is False
    assert sorted(files) == sorted([committed, staged, untracked])


def test_a_rename_reports_only_the_new_name(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "mv", "tracked.txt", "renamed.txt"], check=True
    )

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is False
    assert files == ["renamed.txt"], "the rename source leaked into the change list"


def test_more_than_twenty_changes_stop_at_the_limit(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    names = [f"file-{index:02d}.txt" for index in range(25)]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is True
    assert len(files) == 20
    assert files == sorted(names)[:20]


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_the_caller_branch_packet_records_the_exact_names(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    names = ["中文文件.txt", "  padded.txt  ", "line\nbreak.txt"]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    packet = build_issue_fix_caller_repo_branch_packet(
        repo_path=str(repo),
        base_branch="main",
        issue_branch="issue",
        validation_command=shlex.join([sys.executable, "-c", "print(1)"]),
        execute=True,
    )

    branch = packet["caller_repo_branch"]
    assert branch["branch_ready"] is True
    assert branch["changed_file_count"] == len(names)
    assert sorted(branch["changed_files"]) == sorted(names)
    assert branch["changed_files_truncated"] is False
