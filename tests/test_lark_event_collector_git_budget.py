"""The Lark collector's config admission asks Git three questions.

Each of those reads used to be unbounded, so a Git blocked on a stale index
lock, a network mount or an oversized index held the command open with no
answer. These tests pin the budget and the refusal that follows it.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from loopx.extensions.lark import event_collector
from loopx.extensions.lark.event_collector import _project_config_path


def _git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    (project / ".gitignore").write_text(".loopx/\n", encoding="utf-8")
    return project


def _completed(root: Path, returncode: int) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["git"],
        returncode=returncode,
        stdout=f"{root}\n" if returncode == 0 else "",
        stderr="",
    )


def test_an_unanswered_rev_parse_refuses_instead_of_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    target = project / ".loopx" / "config" / "collector.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    def no_answer(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=5.0)

    monkeypatch.setattr(event_collector.subprocess, "run", no_answer)

    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")


def test_an_unanswered_ignore_probe_refuses_the_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    target = project / ".loopx" / "config" / "collector.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    real_run = subprocess.run

    def bounded(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--show-toplevel":
            return _completed(project, 0)
        if command[3] == "ls-files":
            return _completed(project, 1)
        raise subprocess.TimeoutExpired(cmd="git", timeout=kwargs["timeout"])

    monkeypatch.setattr(event_collector.subprocess, "run", bounded)
    try:
        with pytest.raises(ValueError, match="needs a Git answer"):
            _project_config_path(project, ".loopx/config/collector.json")
    finally:
        monkeypatch.setattr(event_collector.subprocess, "run", real_run)


@pytest.mark.skipif(os.name != "posix", reason="the fake Git is a POSIX shell script")
def test_a_git_that_never_answers_stays_inside_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _git_project(tmp_path)
    target = project / ".loopx" / "config" / "collector.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\nsleep 30\n", encoding="utf-8")
    fake_git.chmod(0o755)

    monkeypatch.setenv("PATH", os.pathsep.join([str(fake_bin), os.environ["PATH"]]))
    monkeypatch.setattr(event_collector, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    with pytest.raises(ValueError, match="needs a Git answer"):
        _project_config_path(project, ".loopx/config/collector.json")
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, f"the probe held the command open for {elapsed:.2f}s"


def test_an_ignored_untracked_config_still_loads(tmp_path: Path) -> None:
    project = _git_project(tmp_path)
    target = project / ".loopx" / "config" / "collector.json"
    target.parent.mkdir(parents=True)
    target.write_text("{}", encoding="utf-8")

    assert _project_config_path(project, ".loopx/config/collector.json") == target.resolve()
