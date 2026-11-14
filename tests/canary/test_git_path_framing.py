"""NUL-framed git pathname reads keep one path one record."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from loopx.canary.maintainability_ratchet import tracked_python_paths
from loopx.canary.runner import _tracked_change_paths
from loopx.cli_commands.canary import _run_git_name_only, collect_git_diff_changed_files
from tests.source_cli import run_source_cli

NEL = "\x85"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _repo_with_odd_python_path(tmp_path: Path) -> tuple[Path, str]:
    """A repository that emits a raw U+0085 in a pathname.

    `core.quotePath=false` is a legal setting; `git ls-files` and
    `git diff --name-only` then print the pathname verbatim, and LF framing
    splits it into two records.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    _git(repo, "config", "core.quotePath", "false")
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "base")
    name = f"odd{NEL}name.py"
    (repo / name).write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", "add a pathname with a raw separator")
    return repo, name


def test_tracked_python_paths_keeps_one_record_per_path(tmp_path: Path) -> None:
    repo, name = _repo_with_odd_python_path(tmp_path)

    paths = tracked_python_paths(repo)

    assert repo / name in paths
    # The torn prefix must not survive as a path of its own.
    assert repo / "odd" not in paths


def test_run_git_name_only_keeps_one_record_per_path(tmp_path: Path) -> None:
    repo, name = _repo_with_odd_python_path(tmp_path)

    result = _run_git_name_only(repo, ["diff", "--name-only", "HEAD~1...HEAD"])

    assert result["ok"] is True
    assert result["changed_files"] == [name]


def test_tracked_change_paths_keeps_one_record_per_path(tmp_path: Path) -> None:
    repo, committed = _repo_with_odd_python_path(tmp_path)
    (repo / committed).write_text("y = 3\n", encoding="utf-8")
    staged = f"staged{NEL}name.py"
    (repo / staged).write_text("z = 4\n", encoding="utf-8")
    _git(repo, "add", staged)

    ok, paths, detail = _tracked_change_paths(repo)

    assert ok is True, detail
    assert paths == sorted([committed.encode("utf-8"), staged.encode("utf-8")])


def test_run_git_name_only_preserves_cr_and_surrounding_spaces(tmp_path: Path) -> None:
    """Text decoding must not rewrite a valid Git pathname.

    Universal-newline translation turns a literal carriage return into LF, and
    trimming removes valid leading or trailing filename spaces. Both altered
    the canary selector's path identity before it could match changed files.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "base")

    names = [" leading.py", "cr\rname.py", "trailing.py "]
    for name in names:
        (repo / name).write_text("x = 2\n", encoding="utf-8")
        _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", "add exact path records")

    result = _run_git_name_only(repo, ["diff", "--name-only", "HEAD~1...HEAD"])
    assert result["ok"] is True
    assert result["changed_files"] == sorted(names)

    selector = collect_git_diff_changed_files(repo_root=repo, base_ref="HEAD~1")
    assert selector["changed_files"] == sorted(names)
    assert selector["changed_file_count"] == len(names)

@pytest.mark.skipif(os.name != "posix", reason="non-UTF-8 Git paths are POSIX-only")
def test_git_byte_paths_fail_closed_before_display_and_dedupe(tmp_path: Path) -> None:
    """An undecodable name must not collide with a literal repr-like name."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "base")

    literal_repr = "b'\\xff.py'"
    (repo / literal_repr).write_text("literal\n", encoding="utf-8")
    _git(repo, "add", literal_repr)
    blob = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"raw\n", stdout=subprocess.PIPE, check=True,
    ).stdout.strip()
    raw_path = b"\xff.py"
    subprocess.run(
        ["git", "-C", str(repo), "update-index", "-z", "--index-info"],
        input=b"100644 " + blob + b" 0\t" + raw_path + b"\0", check=True,
    )
    _git(repo, "commit", "-m", "literal and raw byte paths")

    raw_records = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "-z", "HEAD~1...HEAD"],
        stdout=subprocess.PIPE, check=True,
    ).stdout
    assert {record for record in raw_records.split(b"\0") if record} == {
        literal_repr.encode("utf-8"),
        raw_path,
    }

    source = _run_git_name_only(repo, ["diff", "--name-only", "HEAD~1...HEAD"])
    assert source["ok"] is False
    assert source["undecodable_path_count"] == 1
    assert source["changed_files"] == []

    selector = collect_git_diff_changed_files(repo_root=repo, base_ref="HEAD~1")
    assert selector["ok"] is False
    assert selector["undecodable_path_count"] >= 1
    assert "not valid UTF-8" in selector["error"]
    assert selector["changed_files"] == []

    response = run_source_cli(
        [
            "--format", "json", "canary", "plan", "--from-git-diff",
            "--git-diff-base", "HEAD~1",
        ],
        cwd=repo, check=False, timeout=60,
    )
    assert response.returncode != 0
    assert response.stdout, response.stderr
    payload = json.loads(response.stdout)
    assert payload["ok"] is False
    assert "not valid UTF-8" in payload["error"]
    assert payload["selector_sources"]["git_diff"]["undecodable_path_count"] >= 1
    assert "selection_inputs" not in payload


@pytest.mark.skipif(
    os.name != "posix",
    reason="literal backslash and trailing space pathnames are POSIX-only",
)
def test_premerge_preserves_exact_git_path_identities(tmp_path: Path) -> None:
    """Every Git path identity must survive premerge selection unchanged."""
    from loopx.canary.planner import DEFAULT_CATALOG_PATH

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    catalog = repo / "docs/concepts/interaction-pattern-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(DEFAULT_CATALOG_PATH.read_bytes())
    _git(repo, "add", "docs/concepts/interaction-pattern-catalog.md")
    _git(repo, "commit", "-m", "catalog")

    names = [
        "docs/back\\slash.txt",
        "fixtures/back\\slash.py",
        "fixtures/cr\rname.py",
        "fixtures/leading.py ",
        "fixtures/trailing.py",
        "fixtures/trailing.py ",
    ]
    (repo / "fixtures").mkdir()
    (repo / "docs").mkdir(exist_ok=True)
    for name in names:
        (repo / name).write_text("value = 1\n", encoding="utf-8")
        _git(repo, "add", "--", name)
    _git(repo, "commit", "-m", "exact changed paths")

    selector = collect_git_diff_changed_files(repo_root=repo, base_ref="HEAD~1")
    assert selector["changed_files"] == names
    assert selector["changed_file_count"] == 6

    response = run_source_cli(
        [
            "--format", "json", "canary", "premerge", "--from-git-diff",
            "--git-diff-base", "HEAD~1", "--tier", "quick", "--no-execute",
        ],
        cwd=repo, check=False, timeout=60,
    )
    assert response.returncode == 0, response.stderr
    payload = json.loads(response.stdout)
    assert payload["changed_files"] == names
    assert payload["classification"]["changed_files"] == names
    assert payload["classification"]["changed_file_count"] == 6
    assert payload["catalog_run"]["selection_inputs"]["changed_files"] == names
    boundary_run = payload["boundary_run"]
    assert boundary_run is not None
    assert boundary_run["selection_inputs"]["changed_files"] == names
    assert boundary_run["selection_inputs"]["scan_paths"] == names


@pytest.mark.skipif(os.name != "posix", reason="POSIX CLI path spelling")
def test_premerge_keeps_explicit_changed_file_normalization(tmp_path: Path) -> None:
    """Manual --changed-file paths keep their historical slash normalization."""
    from loopx.canary.planner import DEFAULT_CATALOG_PATH

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    catalog = repo / "docs/concepts/interaction-pattern-catalog.md"
    catalog.parent.mkdir(parents=True)
    catalog.write_bytes(DEFAULT_CATALOG_PATH.read_bytes())
    _git(repo, "add", "docs/concepts/interaction-pattern-catalog.md")
    _git(repo, "commit", "-m", "catalog")

    response = run_source_cli(
        [
            "--format", "json", "canary", "premerge", "--changed-file",
            "docs\\notes.py", "--tier", "quick", "--no-execute",
        ],
        cwd=repo, check=False, timeout=60,
    )
    assert response.returncode == 0, response.stderr
    payload = json.loads(response.stdout)
    assert payload["changed_files"] == ["docs/notes.py"]


def _repo_with_carriage_return_python_path(tmp_path: Path) -> tuple[Path, str]:
    """A repository whose tracked Python module name carries a CR.

    A CR is a legal POSIX filename byte and Git frames it inside one NUL
    record. A text-mode read applies universal-newline translation to the same
    bytes, so ``name\r.py`` arrives as two lines and the rebuilt path names no
    file at all.
    """

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "LoopX Test")
    _git(repo, "config", "user.email", "loopx@example.invalid")
    (repo / "base.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "base.py")
    _git(repo, "commit", "-m", "base")
    name = "carriage\rreturn.py"
    (repo / name).write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", "add a pathname with a CR")
    return repo, name


@pytest.mark.skipif(os.name != "posix", reason="POSIX allows CR in a filename")
def test_tracked_python_paths_keeps_a_carriage_return_name(tmp_path: Path) -> None:
    repo, name = _repo_with_carriage_return_python_path(tmp_path)

    paths = tracked_python_paths(repo)

    assert repo / name in paths
