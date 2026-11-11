from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix.reviewer_recommendation import (
    _collect_history,
    _derive_changed_files,
    build_issue_fix_reviewer_recommendation_packet,
)
from loopx.cli import main as cli_main

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


def test_derive_changed_files_frames_records_on_lf_when_quote_path_is_disabled(
    tmp_path: Path,
) -> None:
    """One changed pathname must stay one record whatever `core.quotePath` is.

    `core.quotePath=false` is a legal repository setting. `git diff --name-only`
    then emits a non-ASCII pathname verbatim, and `str.splitlines()` treated the
    U+0085 inside it as a record boundary, so one changed file was reported as
    the two paths `odd` and `name.txt`.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    _git(repo, "config", "core.quotePath", "false")

    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")

    path = "odd\x85name.txt"
    (repo / path).write_text("x\n", encoding="utf-8")
    _git(repo, "add", path)
    _git(repo, "commit", "-m", "add a pathname with a raw separator")

    assert _derive_changed_files(repo, "HEAD~1") == [path]


@pytest.mark.parametrize(
    "path",
    [
        "中文名字.txt",
        "cr\rname.txt",
        "crlf\r\nname.txt",
        " leading-space.txt",
        "trailing-space.txt ",
        "back\\slash.txt",
    ],
)
def test_derive_changed_files_keeps_paths_git_would_c_quote(
    tmp_path: Path,
    path: str,
) -> None:
    """One `git diff --name-only -z` record must keep the exact pathname.

    With the default `core.quotePath=true`, a line-oriented listing C-quotes
    non-ASCII and control-character names. Replacing the quote escapes with
    slashes turned `中文名字.txt` into `/344/270/255/...`, so CODEOWNERS rules
    and commit history were queried for a path that does not exist. NUL framing
    and strict record decoding must preserve the exact name, including CR,
    CRLF, surrounding spaces and a POSIX literal backslash.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")

    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")

    (repo / path).write_text("x\n", encoding="utf-8")
    _git(repo, "add", "--", path)
    _git(repo, "commit", "-m", "add a path Git would quote")

    assert _derive_changed_files(repo, "HEAD~1") == [path]


@pytest.mark.parametrize(
    ("path", "other"),
    [
        ("note\n*.txt", "note\nX.txt"),
        (":(exclude)skip\n.txt", "other.txt"),
        ("\\notes.txt", "other.txt"),
        (" ", "other.txt"),
        ("back\\..\\slash.txt", "other.txt"),
    ],
)
def test_reviewer_plan_history_binds_the_literal_changed_path(
    tmp_path: Path,
    path: str,
    other: str,
) -> None:
    """History must query the changed path literally, not as a pathspec.

    A pathname containing a wildcard, or starting with pathspec magic such as
    ``:(exclude)``, was trusted to Git as a pathspec by ``git log -- <path>``.
    The query then collected commits for different files and ranked their
    author first as an exact changed-path contributor.
    """
    work_repo = tmp_path / "repo"
    work_repo.mkdir()
    _git(work_repo, "init", "-b", "main")
    _git(work_repo, "config", "user.name", "LoopX Test")
    _git(work_repo, "config", "user.email", "loopx@example.invalid")

    (work_repo / path).write_text("base\n", encoding="utf-8")
    (work_repo / other).write_text("other\n", encoding="utf-8")
    _git(work_repo, "--literal-pathspecs", "add", "--", path, other)
    _git(work_repo, "commit", "-m", "base")

    (work_repo / other).write_text("other change\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="Other Author",
        GIT_AUTHOR_EMAIL="other@example.invalid",
        GIT_COMMITTER_NAME="LoopX Test",
        GIT_COMMITTER_EMAIL="loopx@example.invalid",
    )
    _git(work_repo, "commit", "-am", "other author edits its own file", env=env)

    (work_repo / path).write_text("target change\n", encoding="utf-8")
    _git(work_repo, "--literal-pathspecs", "add", "--", path)
    _git(work_repo, "commit", "-m", "adds the changed path")

    packet = build_issue_fix_reviewer_recommendation_packet(
        repo_path=work_repo,
        repo="approved_local_repo",
        base_ref="HEAD~1",
        history_limit=10,
        execute=True,
        generated_at="2026-08-01T00:00:00Z",
    )

    assert packet["changed_files"] == [path]
    assert [row["display_name"] for row in packet["candidates"]] == ["LoopX Test"]
    assert packet["candidates"][0]["history_commit_count"] == 1
    assert "changed_path_commit_history" in packet["candidates"][0]["reason_codes"]


def test_reviewer_plan_cli_accepts_derived_literal_backslash_root_path(
    tmp_path: Path,
) -> None:
    r"""The public CLI must accept a derived root file whose name starts ``\``."""
    work_repo = tmp_path / "repo"
    work_repo.mkdir()
    _git(work_repo, "init", "-b", "main")
    _git(work_repo, "config", "user.name", "LoopX Test")
    _git(work_repo, "config", "user.email", "loopx@example.invalid")

    path = "\\notes.txt"
    (work_repo / path).write_text("base\n", encoding="utf-8")
    _git(work_repo, "--literal-pathspecs", "add", "--", path)
    _git(work_repo, "commit", "-m", "base")
    (work_repo / path).write_text("target change\n", encoding="utf-8")
    _git(work_repo, "--literal-pathspecs", "add", "--", path)
    _git(work_repo, "commit", "-m", "target change")

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = cli_main(
            [
                "--format",
                "json",
                "issue-fix",
                "reviewer-plan",
                "--repo-path",
                str(work_repo),
                "--repo",
                "approved_local_repo",
                "--base-ref",
                "HEAD~1",
                "--execute",
                "--generated-at",
                "2026-08-01T00:00:00Z",
            ]
        )
    assert result == 0, output.getvalue()
    packet = json.loads(output.getvalue())
    assert packet["ok"] is True
    assert packet["changed_files"] == [path]
    assert [row["display_name"] for row in packet["candidates"]] == ["LoopX Test"]
    assert packet["candidates"][0]["history_commit_count"] == 1


def test_reviewer_plan_preview_keeps_explicit_path_normalisation(
    tmp_path: Path,
) -> None:
    """Explicit --changed-file input keeps its established separator normalisation."""
    missing_repo = tmp_path / "not-read"
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = cli_main(
            [
                "--format",
                "json",
                "issue-fix",
                "reviewer-plan",
                "--repo-path",
                str(missing_repo),
                "--repo",
                "approved_local_repo",
                "--changed-file",
                "docs\\one.py",
                "--generated-at",
                "2026-08-01T00:00:00Z",
            ]
        )
    assert result == 0, output.getvalue()
    packet = json.loads(output.getvalue())
    assert packet["changed_files"] == ["docs/one.py"]
    assert packet["recommendation_status"] == "preview_only"
    assert packet["private_repo_state_read"] is False
    assert not missing_repo.exists()


def test_derive_changed_files_keeps_ordinary_ascii_paths(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")

    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "base.txt")
    _git(repo, "commit", "-m", "base")

    (repo / "added.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "added.txt")
    _git(repo, "commit", "-m", "add a plain path")

    assert _derive_changed_files(repo, "HEAD~1") == ["added.txt"]
