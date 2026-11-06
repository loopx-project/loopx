"""The change-quality scope reads Git under one budget.

The scope used to call `subprocess.run` with no timeout, so a checkout blocked
on a stale index lock, a network mount or an oversized history held the receipt
open with no answer. These tests pin the budget, the retirement of the session
a blocked read started, and the cancellation path a terminal Ctrl-C takes.
"""

from __future__ import annotations

import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx.capabilities.change_quality import scope


def _worktree(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    for command in (
        ["git", "init", "-q", str(repo)],
        ["git", "-C", str(repo), "config", "user.email", "fixture@example.invalid"],
        ["git", "-C", str(repo), "config", "user.name", "Scope Fixture"],
    ):
        subprocess.run(command, check=True)
    (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def _fake_git(tmp_path: Path, script: str, monkeypatch: pytest.MonkeyPatch) -> Path:
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "git"
    fake.write_text(script, encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return bin_dir


def test_a_git_that_never_answers_stops_inside_the_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _worktree(tmp_path)
    _fake_git(tmp_path, "#!/bin/sh\nsleep 30\n", monkeypatch)
    monkeypatch.setattr(scope, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)

    started = time.monotonic()
    with pytest.raises(ValueError, match="did not answer"):
        scope.resolve_git_root(repo)
    elapsed = time.monotonic() - started

    assert elapsed < 6, f"a blocked Git held the read for {elapsed:.1f}s"


@pytest.mark.skipif(os.name != "posix", reason="the fake git is a POSIX script")
def test_a_blocked_read_takes_the_helper_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocked query retires the session, so its child stops too."""

    repo = _worktree(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    stop = tmp_path / "stop"
    real_git = shutil.which("git")
    assert real_git is not None
    _fake_git(
        tmp_path,
        "#!/bin/sh\n"
        "for argument in \"$@\"; do\n"
        "  if [ \"$argument\" = \"--show-toplevel\" ]; then\n"
        f"    while [ ! -f {shlex.quote(str(stop))} ]; do\n"
        f"      echo tick >> {shlex.quote(str(heartbeats))}\n"
        "      sleep 0.1\n"
        "    done\n"
        "    exit 1\n"
        "  fi\n"
        "done\n"
        f'exec {shlex.quote(real_git)} "$@"\n',
        monkeypatch,
    )
    monkeypatch.setattr(scope, "GIT_PROBE_TIMEOUT_SECONDS", 0.8)

    try:
        started = time.monotonic()
        with pytest.raises(ValueError, match="did not answer"):
            scope.resolve_git_root(repo)
        assert time.monotonic() - started < 6
        retired = heartbeats.stat().st_size
        assert retired > 0, "the fixture never blocked, so this proves nothing"
        time.sleep(0.6)
        assert heartbeats.stat().st_size == retired, (
            "the blocked read's helper outlived the budget"
        )
    finally:
        stop.write_text("stop\n", encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="a terminal Ctrl-C is a POSIX group signal")
def test_a_cancelled_read_takes_the_helper_it_started(tmp_path: Path) -> None:
    """Ctrl-C during the read retires the session that read owns."""

    repo = _worktree(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    stop = tmp_path / "stop"
    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "blocking-bin"
    bin_dir.mkdir()
    fake = bin_dir / "git"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "if '--show-toplevel' not in sys.argv:\n"
        f"    os.execv({real_git!r}, [{real_git!r}] + sys.argv[1:])\n"
        f"heartbeats, stop = Path({str(heartbeats)!r}), Path({str(stop)!r})\n"
        "while not stop.exists():\n"
        "    with heartbeats.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('tick\\n')\n"
        "    time.sleep(0.1)\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST", "COVERAGE", "COV_CORE"))
    }
    environment.update({
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "LOOPX_USAGE_PING": "0",
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
    })
    command = subprocess.Popen(
        [
            sys.executable, "-c",
            "import sys\n"
            "from pathlib import Path\n"
            "from loopx.capabilities.change_quality.scope import build_change_quality_scope\n"
            "build_change_quality_scope(repo_path=Path(sys.argv[1]))\n",
            str(repo),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 25
        while heartbeats.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        assert heartbeats.stat().st_size > 0, "the fixture never blocked, so this proves nothing"
        assert command.poll() is None, "the read had already returned"
        os.killpg(os.getpgid(command.pid), signal.SIGINT)
        command.communicate(timeout=25)
        assert command.returncode != 0
        retired = heartbeats.stat().st_size
        time.sleep(0.6)
        assert heartbeats.stat().st_size == retired, (
            "the blocked read and its helper outlived the cancellation"
        )
    finally:
        stop.write_text("stop\n", encoding="utf-8")
        if command.poll() is None:
            os.killpg(os.getpgid(command.pid), signal.SIGKILL)
            command.communicate(timeout=25)


def test_a_healthy_repository_still_builds_its_scope(tmp_path: Path) -> None:
    repo = _worktree(tmp_path)
    (repo / "untracked.txt").write_text("new\n", encoding="utf-8")

    result = scope.build_change_quality_scope(repo_path=repo, base_ref="HEAD")

    assert result["base_commit"] == result["head_commit"]
    assert result["sources"]["untracked"] == ["untracked.txt"]
    assert result["changed_file_count"] == len(result["changed_files"])
    assert scope.resolve_git_root(repo) == repo.resolve()
