from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from loopx import doctor_git


def _worktree(tmp_path: Path) -> Path:
    """A real single-commit repository, so Git really reads an index."""

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "f@e.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Fixture"], check=True)
    (repo / "tracked.txt").write_text("original\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fixture"], check=True)
    return repo


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    return repo


def test_a_git_that_cannot_start_degrades_to_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing Git leaves the diagnostic saying "unknown", not raising."""

    repo = _repo(tmp_path)

    def cannot_start(command, **kwargs):
        raise OSError("git is not installed")

    monkeypatch.setattr(doctor_git.subprocess, "Popen", cannot_start)

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


@pytest.mark.skipif(os.name != "posix", reason="core.fsmonitor needs a POSIX hook")
def test_a_blocked_query_takes_the_helpers_it_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Git's own helpers are in its group, and the budget has to retire them.

    The hook here is an fsmonitor that never answers: Git waits on it, and the
    hook keeps a child writing heartbeats. Killing only the Git process leaves
    both alive, so the heartbeat is what tells the two outcomes apart.
    """

    repo = _worktree(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    hook = repo / ".git" / "blocking-fsmonitor"
    hook.write_text(
        "#!/bin/sh\n"
        f"while :; do echo tick >> {shlex.quote(str(heartbeats))}; sleep 0.1; done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True
    )

    monkeypatch.setattr(doctor_git, "GIT_PROBE_TIMEOUT_SECONDS", 0.8)
    started = time.monotonic()
    assert doctor_git._run_git(repo, "status", "--porcelain") is None
    assert time.monotonic() - started < 6

    retired = heartbeats.stat().st_size
    assert retired > 0, "the fixture hook never ran, so this proves nothing"
    time.sleep(0.6)
    assert heartbeats.stat().st_size == retired, (
        "the fsmonitor hook and its child outlived the query budget"
    )


def test_an_unanswered_root_probe_is_not_reported_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A checkout with its own `.git` stays a Git source when Git is silent."""

    repo = _repo(tmp_path)
    monkeypatch.setattr(doctor_git, "GIT_PROBE_TIMEOUT_SECONDS", 0.3)
    monkeypatch.setattr(doctor_git, "_run_git", lambda *args, **kwargs: None)

    metadata, state = doctor_git.git_source_metadata(repo)

    assert state is doctor_git.GitSourceState.UNCONFIRMED
    assert metadata["git_commit"] is None
    plain = tmp_path / "plain"
    plain.mkdir()
    assert doctor_git.git_source_metadata(plain)[1] is doctor_git.GitSourceState.ABSENT


@pytest.mark.skipif(os.name != "posix", reason="a terminal Ctrl-C is a POSIX group signal")
def test_a_cancelled_query_takes_the_helpers_it_started(tmp_path: Path) -> None:
    """Ctrl-C during a query retires the session that query owns.

    The manifest writer is the terminal entry point that reads a source root's
    identity, and it is what an install run calls. Start it as a terminal
    would, let real Git block on a real ``core.fsmonitor`` hook, then send the
    interrupt to the writer's own process group. Git runs in a session of its
    own, so the interrupt never reaches it: only the writer can retire it.
    """

    repo = _worktree(tmp_path)
    heartbeats = tmp_path / "heartbeats"
    heartbeats.write_text("", encoding="utf-8")
    stop = tmp_path / "stop"
    hook = repo / ".git" / "blocking-fsmonitor"
    hook.write_text(
        "#!/bin/sh\n"
        f"while [ ! -f {shlex.quote(str(stop))} ]; do\n"
        f"  echo tick >> {shlex.quote(str(heartbeats))}\n"
        "  sleep 0.1\n"
        "done\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", str(hook)], check=True
    )
    release_root = tmp_path / "release"
    release_root.mkdir()

    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("PYTEST", "COVERAGE", "COV_CORE"))
    }
    environment.update({
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "LOOPX_USAGE_PING": "0",
    })
    writer = subprocess.Popen(
        [
            sys.executable, "-m", "loopx.release_manifest", str(release_root),
            "--release-id", "cancelled", "--source-root", str(repo),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 20
        while heartbeats.stat().st_size == 0 and time.monotonic() < deadline:
            time.sleep(0.05)
        running = heartbeats.stat().st_size
        assert running > 0, "the fixture hook never ran, so this proves nothing"
        assert writer.poll() is None, "the writer had already returned"
        os.killpg(os.getpgid(writer.pid), signal.SIGINT)
        writer.communicate(timeout=20)
        assert writer.returncode != 0
        assert not (release_root / "release.json").exists(), (
            "a cancelled run still claimed a manifest"
        )
        retired = heartbeats.stat().st_size
        time.sleep(0.6)
        assert heartbeats.stat().st_size == retired, (
            "the fsmonitor hook and its child outlived the cancellation"
        )
    finally:
        stop.write_text("stop\n", encoding="utf-8")
        if writer.poll() is None:
            os.killpg(os.getpgid(writer.pid), signal.SIGKILL)
            writer.communicate(timeout=20)
