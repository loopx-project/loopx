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


@pytest.mark.skipif(os.name != "posix", reason="a newline in a directory is POSIX-only")
def test_repository_root_with_control_characters_is_preserved(tmp_path: Path) -> None:
    """A checkout directory ending in LF must not be trimmed to another path."""

    repo = _repository(tmp_path, "repo\nline")
    (repo / "base.txt").write_text("changed\n", encoding="utf-8")

    scope = _scope(repo)
    assert scope["changed_files"] == ["base.txt"]


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
    ["  padded.py  ", "line\nbreak.txt", "back\\slash.py", "café.txt"],
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
    names = ["  padded.py  ", "line\nbreak.txt", "back\\slash.py", "café.txt"]
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
