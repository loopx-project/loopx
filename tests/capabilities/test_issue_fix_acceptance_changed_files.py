from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix import acceptance_loop
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


@pytest.mark.skipif(os.name != "posix", reason="control characters in names are POSIX-only")
def test_a_carriage_return_is_not_folded_into_a_newline(tmp_path: Path) -> None:
    """Text-mode capture would turn CR and CRLF into LF and collapse them."""

    repo = _repository(tmp_path)
    names = ["line\nbreak.txt", "line\rreturn.txt", "line\r\nboth.txt"]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is False
    assert sorted(files) == sorted(names)


@pytest.mark.skipif(os.name != "posix", reason="control characters in names are POSIX-only")
def test_committed_and_staged_carriage_return_names_survive(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    committed = "committed\r\n.txt"
    staged = "staged\r.txt"

    (repo / committed).write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", committed], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "add committed"], check=True)
    (repo / staged).write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", staged], check=True)

    files, truncated = _changed_files(repo, base_branch="main")

    assert truncated is False
    assert sorted(files) == sorted([committed, staged])


@pytest.mark.skipif(os.name != "posix", reason="control characters in names are POSIX-only")
def test_the_packet_counts_lf_cr_and_crlf_names_separately(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    names = ["line\nbreak.txt", "line\rreturn.txt", "line\r\nboth.txt"]
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
    assert branch["changed_file_count"] == 3
    assert sorted(branch["changed_files"]) == sorted(names)


@pytest.mark.skipif(os.name != "posix", reason="control characters in names are POSIX-only")
def test_packet_keeps_every_line_ending_variant_from_four_sources(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    names: list[str] = []

    committed = [f"committed-{kind}.txt" for kind in ("lf\n", "cr\r", "crlf\r\n")]
    for name in committed:
        (repo / name).write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", *committed], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "committed"], check=True)
    names.extend(committed)

    unstaged = [f"unstaged-{kind}.txt" for kind in ("lf\n", "cr\r", "crlf\r\n")]
    for name in unstaged:
        (repo / name).write_text("before\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", *unstaged], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "tracked"], check=True)
    for name in unstaged:
        (repo / name).write_text("after\n", encoding="utf-8")
    names.extend(unstaged)

    staged = [f"staged-{kind}.txt" for kind in ("lf\n", "cr\r", "crlf\r\n")]
    for name in staged:
        (repo / name).write_text("staged\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", *staged], check=True)
    names.extend(staged)

    untracked = [f"untracked-{kind}.txt" for kind in ("lf\n", "cr\r", "crlf\r\n")]
    for name in untracked:
        (repo / name).write_text("untracked\n", encoding="utf-8")
    names.extend(untracked)

    packet = build_issue_fix_caller_repo_branch_packet(
        repo_path=str(repo),
        base_branch="main",
        issue_branch="issue",
        validation_command=shlex.join([sys.executable, "-c", "print(1)"]),
        execute=True,
    )

    branch = packet["caller_repo_branch"]
    assert branch["branch_ready"] is True
    assert branch["changed_file_count"] == 12
    assert branch["changed_files_truncated"] is False
    assert sorted(branch["changed_files"]) == sorted(names)


@pytest.mark.skipif(os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only")
def test_packet_distinguishes_byte_paths_from_a_real_replacement_character(
    tmp_path: Path,
) -> None:
    repo = _repository(tmp_path)
    replacement = "lossy-\ufffd.txt"
    (repo / replacement).write_text("valid replacement\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", replacement], check=True)

    fe_blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"fe\n", stdout=subprocess.PIPE, check=True,
    ).stdout.strip()
    ff_blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"ff\n", stdout=subprocess.PIPE, check=True,
    ).stdout.strip()
    index_info = (
        b"100644 " + fe_blob + b" 0\tlossy-\xfe.txt\0"
        + b"100644 " + ff_blob + b" 0\tlossy-\xff.txt\0"
    )
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "-z", "--index-info"],
        input=index_info, check=True,
    )

    raw = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "--name-only", "-z"],
        stdout=subprocess.PIPE, check=True,
    ).stdout
    packet = build_issue_fix_caller_repo_branch_packet(
        repo_path=str(repo),
        base_branch="main",
        issue_branch="issue",
        validation_command=shlex.join([sys.executable, "-c", "print(1)"]),
        execute=True,
    )
    expected = [
        replacement,
        "git-bytes:" + repr(b"lossy-\xfe.txt"),
        "git-bytes:" + repr(b"lossy-\xff.txt"),
    ]

    assert len([record for record in raw.split(b"\0") if record]) == 3
    assert packet["caller_repo_branch"]["changed_file_count"] == 3
    assert len(set(packet["caller_repo_branch"]["changed_files"])) == 3
    assert set(packet["caller_repo_branch"]["changed_files"]) == set(expected)
    assert set(packet["review_packet"]["files_changed"]) == set(expected)
    assert packet["review_packet"]["ready"] is True


@pytest.mark.skipif(os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only")
def test_untracked_byte_paths_keep_three_distinct_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repository(tmp_path)
    output = (
        b"?? lossy-\xef\xbf\xbd.txt\0"
        b"?? lossy-\xfe.txt\0"
        b"?? lossy-\xff.txt\0"
    )

    def fake_run_git_paths(workspace: Path, args: list[str], *, timeout: int = 10):
        stdout = output if args == ["status", "--porcelain", "-z"] else b""
        return subprocess.CompletedProcess(args, 0, stdout, b"")

    monkeypatch.setattr(acceptance_loop, "_run_git_paths", fake_run_git_paths)
    paths = acceptance_loop._git_untracked_paths(repo)
    files, truncated = _changed_files(repo, base_branch="main")
    displayed = [acceptance_loop._display_git_path(path) for path in paths]

    assert paths == [
        b"lossy-\xef\xbf\xbd.txt",
        b"lossy-\xfe.txt",
        b"lossy-\xff.txt",
    ]
    assert truncated is False
    assert files == displayed
    assert displayed[0] == "lossy-\ufffd.txt"
    assert len(set(displayed)) == 3
