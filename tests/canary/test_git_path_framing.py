"""NUL-framed git pathname reads keep one path one record."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loopx.canary.maintainability_ratchet import tracked_python_paths
from loopx.cli_commands.canary import _run_git_name_only

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


def test_run_git_name_only_handles_timeout(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=0.1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    res = _run_git_name_only(tmp_path, ["diff"], timeout=0.1)
    assert res["ok"] is False
    assert res["returncode"] == -1
    assert "git execution failed" in str(res["stderr_tail"])


def test_resolve_git_repo_root_handles_timeout(tmp_path: Path, monkeypatch) -> None:
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=0.1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    from loopx.cli_commands.canary import _resolve_git_repo_root

    resolved = _resolve_git_repo_root(tmp_path, timeout=0.1)
    assert resolved == tmp_path.resolve()
