from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from loopx.capabilities.change_quality.result import (
    _validate_evidence_targets,
    _normalize_evidence_ref,
    _normalize_validation,
    _relative_path,
)
from loopx.capabilities.change_quality.scope import build_change_quality_scope


def _repository(tmp_path: Path, dirname: str = "repo") -> Path:
    repo = tmp_path / dirname
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


def _commit_tree_with_paths(repo: Path, base: str, names: list[bytes]) -> None:
    """Commit a synthetic Git tree without requiring those names on disk."""

    blob = subprocess.check_output(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"content\n",
    ).strip()
    tree_input = b"".join(
        b"100644 blob " + blob + b"\t" + name + b"\0" for name in names
    )
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "mktree", "-z"], input=tree_input
    ).strip().decode("ascii")
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "commit-tree", tree, "-p", base, "-m", "fixture"],
        text=True,
    ).strip()
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/heads/main", commit],
        check=True,
    )




def test_committed_cr_names_remain_distinct_from_git_tree(tmp_path: Path) -> None:
    """Real Git bytes prove identity even where the host cannot create the names."""

    repo = _repository(tmp_path)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    names = [b"base.txt", b"line\rbreak.txt", b"line\r\nbreak.txt"]
    _commit_tree_with_paths(repo, base, names)

    scope = build_change_quality_scope(repo_path=repo, base_ref=base)
    assert set(scope["sources"]["committed"]) == {
        "base.txt",
        "line\rbreak.txt",
        "line\r\nbreak.txt",
    }
    assert scope["changed_file_count"] == 3


@pytest.mark.skipif(os.name != "posix", reason="a carriage return in a path is POSIX-only")
@pytest.mark.parametrize("name", ["line\rbreak.txt", "line\r\nbreak.txt"])
def test_untracked_names_with_carriage_returns_stay_exact(
    tmp_path: Path, name: str
) -> None:
    """Text-mode Git reads normalize CR and CRLF; the scope must not."""

    repo = _repository(tmp_path)
    (repo / name).write_text("content\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == [name]
    assert scope["sources"]["untracked"] == [name]


@pytest.mark.skipif(os.name != "posix", reason="a trailing control character in a directory is POSIX-only")
@pytest.mark.parametrize("dirname", ["repo\n", "repo\r", "repo "])
def test_repository_root_with_trailing_whitespace_is_preserved(
    tmp_path: Path, dirname: str
) -> None:
    """A checkout directory ending in whitespace must not be trimmed."""

    repo = _repository(tmp_path, dirname)
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == ["base.txt"]


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_cr_and_lf_names_remain_distinct(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    names = ["line\rbreak.txt", "line\nbreak.txt", "line\r\nbreak.txt"]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == sorted(names)
    assert scope["changed_file_count"] == 3


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_cr_names_are_preserved_across_all_scope_sources(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    committed_name = "committed\r.txt"
    unstaged_name = "unstaged\r.txt"
    for name in (committed_name, unstaged_name):
        (repo / name).write_text("first\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(repo), "add", "--", committed_name, unstaged_name],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-qm", "add control-character paths"],
        check=True,
    )
    (repo / unstaged_name).write_text("second\n", encoding="utf-8")
    staged_name = "staged\r.txt"
    untracked_name = "untracked\r.txt"
    (repo / staged_name).write_text("staged\n", encoding="utf-8")
    (repo / untracked_name).write_text("untracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "--", staged_name], check=True)

    scope = build_change_quality_scope(repo_path=repo, base_ref=base)
    assert scope["sources"]["committed"] == sorted([committed_name, unstaged_name])
    assert scope["sources"]["staged"] == [staged_name]
    assert scope["sources"]["unstaged"] == [unstaged_name]
    assert scope["sources"]["untracked"] == [untracked_name]
    assert set(scope["changed_files"]) == {
        committed_name, unstaged_name, staged_name, untracked_name
    }
    assert scope["changed_file_count"] == 4


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


@pytest.mark.parametrize(
    "name",
    [
        "  padded.py  ",
        "line\rbreak.txt",
        "line\r\nbreak.txt",
        "line\nbreak.txt",
        "back\\slash.py",
        "café.txt",
    ],
)
def test_record_evidence_keeps_the_exact_scope_path(name: str) -> None:
    """Evidence must name the file the scope reported, not a normalized copy."""

    ref = _normalize_evidence_ref(f"path:{name}", field="evidence_refs[]")
    assert ref == f"path:{name}"
    assert _relative_path(name, field="risk.path") == name

    evidence = _normalize_validation(
        {
            "validator": "pytest",
            "status": "passed",
            "scope": "unit",
            "covers_paths": [name],
        },
        index=0,
    )
    assert evidence["covers_paths"] == [name]


@pytest.mark.parametrize("target", ["/abs/file.txt", "../outside.py", "a/../../b"])
def test_record_evidence_still_rejects_escapes(target: str) -> None:
    with pytest.raises(ValueError, match="repo-relative"):
        _normalize_evidence_ref(f"path:{target}", field="evidence_refs[]")


@pytest.mark.skipif(os.name != "posix", reason="special names are POSIX-only")
def test_scope_names_match_the_path_evidence_they_are_recorded_with(
    tmp_path: Path,
) -> None:
    """The names the scope reports must be the names evidence resolves against."""

    repo = _repository(tmp_path)
    names = [
        "  padded.py  ",
        "line\rbreak.txt",
        "line\r\nbreak.txt",
        "line\nbreak.txt",
        "back\\slash.py",
        "café.txt",
    ]
    for name in names:
        (repo / name).write_text("content\n", encoding="utf-8")

    changed = set(_scope(repo)["changed_files"])
    assert changed == set(names)

    for name in names:
        ref = _normalize_evidence_ref(f"path:{name}", field="evidence_refs[]")
        _validate_evidence_targets(
            [ref],
            field="evidence_refs",
            changed_files=changed,
            instruction_refs=set(),
            validator_ids=set(),
        )


def test_non_utf8_changed_path_fails_explicitly(tmp_path: Path) -> None:
    repo = _repository(tmp_path)
    base = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()
    _commit_tree_with_paths(repo, base, [b"bad-\xff.txt"])

    with pytest.raises(ValueError, match="not valid UTF-8"):
        build_change_quality_scope(repo_path=repo, base_ref=base)
