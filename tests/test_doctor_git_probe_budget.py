from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx import doctor_git


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_git_answers_degrade_when_the_budget_runs_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Git that never answers leaves the diagnostic saying "unknown"."""

    repo = _repo(tmp_path)

    def always_timeout(command, **kwargs):
        raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

    monkeypatch.setattr(doctor_git.subprocess, "run", always_timeout)

    assert doctor_git._is_git_worktree_root(repo) is False
    assert doctor_git.git_metadata_for_root(repo) == {
        "root": str(repo.resolve()),
        "git_commit": None,
        "git_ref": None,
        "git_dirty": None,
    }
    assert (
        doctor_git.git_revision_relation(
            repo, installed_commit="a" * 40, comparison_commit="b" * 40
        )
        is doctor_git.GitRevisionRelation.UNKNOWN
    )
    assert (
        doctor_git.trusted_release_ref_for_root(
            repo, repository="example/project", ref="main"
        )
        is None
    )


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_doctor_finishes_when_git_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked Git must not hold doctor open: every entry point returns."""

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", encoding="utf-8")
    fake.chmod(0o755)
    repo = _repo(tmp_path)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr(doctor_git, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    assert doctor_git._is_git_worktree_root(repo) is False
    assert doctor_git.git_metadata_for_root(repo)["git_dirty"] is None
    assert (
        doctor_git.git_revision_relation(
            repo, installed_commit="a" * 40, comparison_commit="b" * 40
        )
        is doctor_git.GitRevisionRelation.UNKNOWN
    )
    assert (
        doctor_git.trusted_release_ref_for_root(
            repo, repository="example/project", ref="main"
        )
        is None
    )
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"a blocked Git held doctor for {elapsed:.1f}s"
